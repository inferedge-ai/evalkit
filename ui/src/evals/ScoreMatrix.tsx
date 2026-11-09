import { createContext, useContext, useEffect, useMemo, useRef } from "react";
import { ArrowDown, ArrowUp } from "lucide-react";
import type { ReactNode } from "react";
import type {
  CellClassParams,
  ColDef,
  ColGroupDef,
  RowHeightParams,
  SizeColumnsToContentStrategy,
  SuppressHeaderKeyboardEventParams,
} from "ag-grid-community";
import type {
  CustomCellRendererProps,
  CustomInnerHeaderProps,
} from "ag-grid-react";
import {
  ColumnsMenu,
  DataGrid,
  compareNullsLast,
  readValue,
  useDataGrid,
} from "../lib/DataGrid";
import type { GridColumn } from "../lib/DataGrid";
import { fmtDelta, fmtGauge, fmtScore, severity } from "../lib/format";
import { describeTerm } from "../lib/glossary";
import { InfoTip } from "../lib/ui";
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

const getMatrixRowId = (r: MatrixRow) => r.case_id;

// Pinned under the cases: each column's mean.
const MEAN_ROWS: MatrixRow[] = [{ case_id: "mean", row: null }];

const getMeanRowHeight = ({ node }: RowHeightParams<MatrixRow>) =>
  node.rowPinned ? 40 : undefined;

// Past this many cases the matrix scrolls inside a screen-tall grid, so the
// header and the means stay in view.
const SCROLL_AFTER_ROWS = 20;

const FIT_CASE_COLUMN: SizeColumnsToContentStrategy = {
  type: "fitCellContents",
  colIds: ["case"],
};

const shortLabel = (name: string) =>
  name.replace(/^(answer_rubric_|gauge_)/, "").replaceAll("_", " ");

// Wide enough for the label in the small uppercase header font, and for a
// value with its change or the visible rows' mean and count.
const estimateColumnWidth = (label: string, withBase: boolean) =>
  Math.max(withBase ? 120 : 96, label.length * 8 + 40);

const NO_COLUMNS: string[] = [];

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
          {moved > 0 ? (
            <ArrowUp
              size={10}
              role="img"
              aria-label="up"
              className="inline align-[-0.1em]"
            />
          ) : (
            <ArrowDown
              size={10}
              role="img"
              aria-label="down"
              className="inline align-[-0.1em]"
            />
          )}
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
          {fmt(kind, col, visible.mean)} · n={visible.n}
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

interface MatrixState {
  report: Report;
  rows: MatrixRow[];
  filtered: boolean;
  withBase: boolean;
  caseCell: (r: MatrixRow) => ReactNode;
}

// Cells read what changes on every poll from here, so the column definitions,
// and the cells AG Grid mounts for them, stay put while an experiment runs.
const MatrixState = createContext<MatrixState | null>(null);

function MatrixValueCell({
  data,
  node,
  kind,
  col,
}: CustomCellRendererProps<MatrixRow> & { kind: ColumnKind; col: string }) {
  const { report, rows, filtered, withBase } = useContext(MatrixState)!;
  if (node.rowPinned)
    return (
      <Footer
        report={report}
        kind={kind}
        col={col}
        rows={rows}
        filtered={filtered}
        withBase={withBase}
      />
    );
  return data ? (
    <Value r={data} kind={kind} col={col} withBase={withBase} />
  ) : null;
}

function MatrixCaseCell({ data, node }: CustomCellRendererProps<MatrixRow>) {
  const { caseCell, filtered } = useContext(MatrixState)!;
  if (node.rowPinned)
    return (
      <span className="flex h-full flex-col justify-center text-micro leading-4 text-ink-3">
        <span className="font-semibold tracking-wide uppercase">mean</span>
        {filtered && <span>visible rows</span>}
      </span>
    );
  return data ? caseCell(data) : null;
}

// The cells render more of a row than the value they sort by (errors, changes,
// run errors), so AG Grid hands them each refresh's row, not only on a new
// value.
const alwaysRefresh = () => false;

function MatrixHeaderLabel({
  displayName,
  term,
}: CustomInnerHeaderProps<MatrixRow> & { term: string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1">
      <span className="truncate" title={term}>
        {displayName}
      </span>
      <InfoTip term={term} />
    </span>
  );
}

// AG Grid moves Tab from header cell to header cell, and sorts on Enter
// anywhere inside one. The browser takes Tab from the cell to the ⓘ inside,
// and Enter on the ⓘ presses it instead of sorting.
const passKeysToInfoTip = ({ event }: SuppressHeaderKeyboardEventParams) =>
  event.target === event.currentTarget
    ? event.key === "Tab" && !event.shiftKey
    : event.key === "Enter";

function buildValueColumn(
  kind: ColumnKind,
  col: string,
  defaultHidden: string[],
  withBase: boolean,
): ColDef<MatrixRow> {
  const colId = kind === "avg" ? "avg" : col;
  const label = kind === "avg" ? "mean" : shortLabel(col);
  return {
    colId,
    headerName: label,
    ...(describeTerm(col)
      ? {
          headerComponentParams: {
            innerHeaderComponent: MatrixHeaderLabel,
            innerHeaderComponentParams: { term: col },
          },
          suppressHeaderKeyboardEvent: passKeysToInfoTip,
        }
      : {
          headerTooltip:
            kind === "avg" ? "mean of the case's bucketed scores" : col,
        }),
    initialHide: defaultHidden.includes(colId),
    type: "number",
    initialWidth: estimateColumnWidth(label, withBase),
    valueGetter: readValue((r) => sortValue(r, kind, col)),
    comparator: compareNullsLast,
    cellClass: cellClass(kind, col),
    cellRenderer: MatrixValueCell,
    equals: alwaysRefresh,
    cellRendererParams: { kind, col },
  };
}

function buildMatrixColumns(
  groups: ColumnGroup[],
  defaultHidden: string[],
  withBase: boolean,
): GridColumn<MatrixRow>[] {
  return [
    {
      colId: "case",
      headerName: "case",
      pinned: "left",
      lockPinned: true,
      lockVisible: true,
      initialFlex: 0,
      valueGetter: readValue((r) => r.case_id),
      cellClass: "font-mono",
      cellRenderer: MatrixCaseCell,
      equals: alwaysRefresh,
    },
    buildValueColumn("avg", "", defaultHidden, withBase),
    ...groups.map((g): ColGroupDef<MatrixRow> => ({
      groupId: `group:${g.label}`,
      headerName: g.label,
      marryChildren: true,
      children: g.cols.map((col) =>
        buildValueColumn(g.kind, col, defaultHidden, withBase),
      ),
    })),
  ];
}

export function ScoreMatrix({
  id,
  report,
  groups,
  rows,
  filtered,
  caseCell,
  defaultHidden = NO_COLUMNS,
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
  // Callers rebuild the groups from each poll's report; the columns change
  // only when the set of columns does.
  const structure = JSON.stringify([groups, defaultHidden, withBase]);
  const columns = useMemo(
    () => buildMatrixColumns(groups, defaultHidden, withBase),
    [structure],
  );

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
        <MatrixState.Provider
          value={{ report, rows, filtered, withBase, caseCell }}
        >
          <DataGrid
            grid={grid}
            rows={rows}
            columns={columns}
            getRowId={getMatrixRowId}
            height={
              rows.length > SCROLL_AFTER_ROWS
                ? "calc(100dvh - 15rem)"
                : undefined
            }
            pinnedBottomRowData={MEAN_ROWS}
            autoSizeStrategy={FIT_CASE_COLUMN}
            getRowHeight={getMeanRowHeight}
          />
        </MatrixState.Provider>
      </div>
    </>
  );
}
