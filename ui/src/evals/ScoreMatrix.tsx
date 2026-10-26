import { useEffect, useMemo, useRef } from "react";
import type { ReactNode } from "react";
import { ColumnsMenu, DataTable, useDataTable } from "../lib/DataTable";
import type { Column } from "../lib/DataTable";
import { fmtDelta, fmtGauge, fmtScore, severity } from "../lib/format";
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
import { InfoTip } from "../lib/ui";

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

const shortLabel = (name: string) =>
  name.replace(/^(answer_rubric_|gauge_)/, "").replaceAll("_", " ");

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
  return (r: MatrixRow) => {
    const value = r.row ? cellValue(r.row, kind, col) : null;
    if (value === null && kind === "score" && r.row && col in r.row.errors)
      return "score text-err-text";
    const sev = kind === "gauge" ? "none" : severity(value);
    return `score sev-fg-${sev} ${sev === "none" ? "" : `heat-${sev}`}`;
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
    <>
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
    </>
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
  const columns = useMemo((): Column<MatrixRow>[] => {
    const leaf = (kind: ColumnKind, col: string): Column<MatrixRow> => ({
      id: kind === "avg" ? "avg" : col,
      header:
        kind === "avg"
          ? "mean"
          : () => (
              <span className="whitespace-nowrap">
                {shortLabel(col)} <InfoTip term={col} />
              </span>
            ),
      accessorFn: (r) => sortValue(r, kind, col),
      sortUndefined: "last",
      meta: {
        label: kind === "avg" ? "mean" : shortLabel(col),
        title: kind === "avg" ? "mean of the case's bucketed scores" : col,
        className: "score",
        cellClass: cellClass(kind, col),
      },
      cell: (c) => (
        <Value r={c.row.original} kind={kind} col={col} withBase={withBase} />
      ),
      footer: () => (
        <Footer
          report={report}
          kind={kind}
          col={col}
          rows={rows}
          filtered={filtered}
          withBase={withBase}
        />
      ),
    });
    return [
      {
        id: "case",
        header: "case",
        accessorFn: (r) => r.case_id,
        enableHiding: false,
        meta: {
          className: "col-case font-mono",
          headerClassName: "col-case",
        },
        cell: (c) => caseCell(c.row.original),
        footer: () => (
          <span className="text-micro font-semibold tracking-wide text-ink-3 uppercase">
            mean
          </span>
        ),
      },
      leaf("avg", ""),
      ...groups.map((g): Column<MatrixRow> => ({
        id: `group:${g.label}`,
        header: g.label,
        meta: {
          headerClassName:
            "border-l border-line text-micro font-semibold tracking-wide text-ink-3 uppercase",
        },
        columns: g.cols.map((col) => leaf(g.kind, col)),
      })),
      {
        id: "slack",
        header: "",
        enableHiding: false,
        enableSorting: false,
        enableResizing: false,
        meta: { className: "col-slack", headerClassName: "col-slack" },
        cell: () => null,
      },
    ];
  }, [report, groups, rows, filtered, caseCell, withBase]);

  const table = useDataTable({
    id,
    data: rows,
    columns,
    getRowId: (r) => r.case_id,
    defaultHidden,
  });

  const focused = useRef(sortBy);
  useEffect(() => {
    const previous = focused.current;
    focused.current = sortBy;
    if (sortBy) {
      table.getColumn(sortBy)?.toggleVisibility(true);
      table.setSorting([{ id: sortBy, desc: false }]);
    } else if (previous && table.state.sorting[0]?.id === previous) {
      table.setSorting([]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sortBy]);

  return (
    <>
      <div className="toolbar mb-1">
        {toolbar}
        {toggles.map((t) => {
          const cols = t.cols.flatMap((c) => table.getColumn(c) ?? []);
          if (!cols.length) return null;
          return (
            <label className="check" title={t.title} key={t.label}>
              <input
                type="checkbox"
                checked={cols.every((c) => c.getIsVisible())}
                onChange={(e) =>
                  cols.forEach((c) => c.toggleVisibility(e.target.checked))
                }
              />
              {t.label}
            </label>
          );
        })}
        <span className="ml-auto">
          <ColumnsMenu table={table} defaultHidden={defaultHidden} />
        </span>
      </div>
      <DataTable
        table={table}
        className="matrix"
        containerClassName="card mt-2 max-h-[calc(100dvh-15rem)] overflow-auto"
      />
    </>
  );
}
