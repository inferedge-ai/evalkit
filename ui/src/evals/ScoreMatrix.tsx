import { useEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import type {
  CellClassParams,
  ColDef,
  ColGroupDef,
  RowHeightParams,
} from "ag-grid-community";
import type { CustomCellRendererProps } from "ag-grid-react";
import {
  ColumnsMenu,
  DataGrid,
  missingLast,
  readValue,
  useDataGrid,
} from "../lib/DataGrid";
import type { GridColumn } from "../lib/DataGrid";
import { fmtDelta, fmtGauge, fmtScore, severity } from "../lib/format";
import { describeTerm } from "../lib/glossary";
import {
  cellValue,
  delta,
  mean,
  serverMean,
  visibleStat,
  type ColumnGroup,
  type ColumnKind,
} from "../lib/scoreSummary";
import type { CaseRow, Report } from "../lib/types";

export interface ColumnToggle {
  label: string;
  title: string;
  cols: string[];
}

export interface MatrixRow {
  case_id: string;
  row: CaseRow | null;
  base?: CaseRow | null;
}

const matrixRowId = (r: MatrixRow) => r.case_id;

// Pinned under the cases: each column's mean.
const MEAN_ROWS: MatrixRow[] = [{ case_id: "mean", row: null }];

const meanRowHeight = ({ node }: RowHeightParams<MatrixRow>) =>
  node.rowPinned ? 40 : undefined;

// Past this many cases the matrix scrolls inside a screen-tall grid, so the
// header and the means stay in view.
const SCROLL_AFTER_ROWS = 20;

const shortLabel = (name: string) =>
  name.replace(/^(answer_rubric_|gauge_)/, "").replaceAll("_", " ");

// Wide enough for the label in the small uppercase header font.
const labelWidth = (label: string) => Math.max(72, label.length * 8 + 40);

function fmt(kind: ColumnKind, col: string, value: number | null): string {
  return kind === "gauge" ? fmtGauge(col, value) : fmtScore(value);
}

function change(
  r: MatrixRow,
  kind: ColumnKind,
  col: string,
  withBase: boolean,
) {
  if (!withBase) return kind === "avg" ? (r.row?.delta ?? null) : null;
  if (!r.row || !r.base) return null;
  return delta(cellValue(r.base, kind, col), cellValue(r.row, kind, col));
}

function cellClass(kind: ColumnKind, col: string) {
  return ({ data, node }: CellClassParams<MatrixRow>) => {
    if (node.rowPinned || !data) return "ag-right-aligned-cell";
    const value = data.row ? cellValue(data.row, kind, col) : null;
    if (
      value === null &&
      kind === "score" &&
      data.row &&
      col in data.row.errors
    )
      return "ag-right-aligned-cell text-err-text";
    const sev = kind === "gauge" ? "none" : severity(value);
    return `ag-right-aligned-cell sev-fg-${sev} ${sev === "none" ? "" : `heat-${sev}`}`;
  };
}

function Value({
  r,
  kind,
  col,
  withBase,
}: {
  r: MatrixRow;
  kind: ColumnKind;
  col: string;
  withBase: boolean;
}) {
  const value = r.row ? cellValue(r.row, kind, col) : null;
  const error = kind === "score" ? r.row?.errors[col] : undefined;
  if (value === null && error !== undefined)
    return <span title={error}>error</span>;
  const moved = change(r, kind, col, withBase);
  const shown = moved !== null && Math.abs(moved) >= 0.005;
  return (
    <span
      title={
        shown
          ? `${error ? `${error} · ` : ""}${fmtDelta(moved)} vs base`
          : error
      }
    >
      {fmt(kind, col, value)}
      {shown && kind !== "gauge" && (
        <span
          className={`ml-1 text-micro ${moved > 0 ? "text-ok" : "text-err-text"}`}
        >
          {moved > 0 ? "▲" : "▼"}
          {Math.abs(moved).toFixed(2)}
        </span>
      )}
    </span>
  );
}

function Footer({
  report,
  kind,
  col,
  rows,
  filtered,
  withBase,
}: {
  report: Report;
  kind: ColumnKind;
  col: string;
  rows: MatrixRow[];
  filtered: boolean;
  withBase: boolean;
}) {
  const all = serverMean(report, kind, col);
  const visible = visibleStat(
    rows.flatMap((r) => (r.row ? [r.row] : [])),
    kind,
    col,
  );
  const meanDelta = withBase
    ? mean(rows.map((r) => change(r, kind, col, true)))
    : null;
  return (
    <div className="flex h-full flex-col items-end justify-center leading-4">
      <div
        className={`font-semibold sev-fg-${kind === "gauge" ? "none" : severity(all)}`}
      >
        {fmt(kind, col, all)}
      </div>
      {meanDelta !== null && !filtered && (
        <div
          className={`text-micro ${meanDelta > 0 ? "text-ok" : meanDelta < 0 ? "text-err-text" : "text-ink-4"}`}
        >
          {fmtDelta(meanDelta)}
        </div>
      )}
      {filtered && (
        <div className="text-micro text-ink-3" title="mean over the rows shown">
          visible {fmt(kind, col, visible.mean)} · n={visible.n}
          {meanDelta !== null && (
            <span
              className={
                meanDelta > 0 ? "text-ok" : meanDelta < 0 ? "text-err-text" : ""
              }
            >
              {" "}
              {fmtDelta(meanDelta)}
            </span>
          )}
        </div>
      )}
    </div>
  );
}

function sortValue(r: MatrixRow, kind: ColumnKind, col: string) {
  if (!r.row) return undefined;
  const value = cellValue(r.row, kind, col);
  if (value !== null) return value;
  return kind === "score" && col in r.row.errors ? -1 : undefined;
}

export function ScoreMatrix({
  id,
  report,
  groups,
  rows,
  filtered,
  caseCell,
  defaultHidden = [],
  sortBy = "",
  toolbar,
  toggles = [],
  withBase = false,
}: {
  id: string;
  report: Report;
  groups: ColumnGroup[];
  rows: MatrixRow[];
  filtered: boolean;
  caseCell: (r: MatrixRow) => ReactNode;
  defaultHidden?: string[];
  sortBy?: string;
  toolbar?: ReactNode;
  toggles?: ColumnToggle[];
  withBase?: boolean;
}) {
  const columns = useMemo((): GridColumn<MatrixRow>[] => {
    const leaf = (kind: ColumnKind, col: string): ColDef<MatrixRow> => {
      const colId = kind === "avg" ? "avg" : col;
      const label = kind === "avg" ? "mean" : shortLabel(col);
      const term = describeTerm(col);
      return {
        colId,
        headerName: label,
        headerTooltip:
          kind === "avg"
            ? "mean of the case's bucketed scores"
            : term
              ? `${col}: ${term}`
              : col,
        initialHide: defaultHidden.includes(colId),
        type: "number",
        initialWidth: labelWidth(label),
        valueGetter: readValue((r) => sortValue(r, kind, col)),
        comparator: missingLast,
        cellClass: cellClass(kind, col),
        cellRenderer: ({ data, node }: CustomCellRendererProps<MatrixRow>) =>
          node.rowPinned ? (
            <Footer
              report={report}
              kind={kind}
              col={col}
              rows={rows}
              filtered={filtered}
              withBase={withBase}
            />
          ) : data ? (
            <Value r={data} kind={kind} col={col} withBase={withBase} />
          ) : null,
      };
    };
    return [
      {
        colId: "case",
        headerName: "case",
        pinned: "left",
        lockPinned: true,
        lockVisible: true,
        initialFlex: 0,
        initialWidth: 300,
        valueGetter: readValue((r) => r.case_id),
        cellClass: "font-mono",
        cellRenderer: ({ data, node }: CustomCellRendererProps<MatrixRow>) =>
          node.rowPinned ? (
            <span className="text-micro font-semibold tracking-wide text-ink-3 uppercase">
              mean
            </span>
          ) : data ? (
            caseCell(data)
          ) : null,
      },
      leaf("avg", ""),
      ...groups.map((g): ColGroupDef<MatrixRow> => ({
        groupId: `group:${g.label}`,
        headerName: g.label,
        marryChildren: true,
        children: g.cols.map((col) => leaf(g.kind, col)),
      })),
    ];
  }, [report, groups, rows, filtered, caseCell, withBase, defaultHidden]);

  const grid = useDataGrid<MatrixRow>(id);
  const api = grid.api;

  const focused = useRef("");
  useEffect(() => {
    if (!api) return;
    const previous = focused.current;
    focused.current = sortBy;
    if (sortBy) {
      api.setColumnsVisible([sortBy], true);
      api.applyColumnState({
        state: [{ colId: sortBy, sort: "asc" }],
        defaultState: { sort: null },
      });
      api.ensureColumnVisible(sortBy, "middle");
    } else if (
      previous &&
      api.getColumnState().find((s) => s.sort)?.colId === previous
    ) {
      api.applyColumnState({ defaultState: { sort: null } });
    }
  }, [api, sortBy]);

  return (
    <>
      <div className="toolbar mb-1">
        {toolbar}
        {toggles.map((t) => {
          const cols = t.cols.flatMap((c) => api?.getColumn(c) ?? []);
          if (!cols.length) return null;
          return (
            <label className="check" title={t.title} key={t.label}>
              <input
                type="checkbox"
                checked={cols.every((c) => c.isVisible())}
                onChange={(e) => api?.setColumnsVisible(cols, e.target.checked)}
              />
              {t.label}
            </label>
          );
        })}
        <span className="ml-auto">
          <ColumnsMenu grid={grid} />
        </span>
      </div>
      <div className="mt-2">
        <DataGrid
          grid={grid}
          rows={rows}
          columns={columns}
          getRowId={matrixRowId}
          height={
            rows.length > SCROLL_AFTER_ROWS ? "calc(100dvh - 15rem)" : undefined
          }
          pinnedBottomRowData={MEAN_ROWS}
          getRowHeight={meanRowHeight}
        />
      </div>
    </>
  );
}
