import type { CaseRow, Report } from "./types";

export function hasColumn(row: CaseRow, col: string): boolean {
  return row.scores[col] != null || col in row.errors;
}

export function columnShown(report: Report, col: string): boolean {
  return report.cases.some((row) => hasColumn(row, col));
}

export function rowFailing(row: CaseRow): boolean {
  return (
    row.run_errors.length > 0 ||
    Object.keys(row.errors).length > 0 ||
    Object.values(row.scores).some((s) => s !== null && s < 0.8)
  );
}

export function worstFirstKey(row: CaseRow, col: string): number {
  return row.scores[col] ?? (col in row.errors ? -1 : 1);
}
