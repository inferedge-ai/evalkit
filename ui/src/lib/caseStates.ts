import type { CaseRow, CaseRunState, Report } from "./types";

export const CASE_STATE_LABEL: Record<CaseRunState, string> = {
  pending: "not run",
  ran: "ran, not evaluated",
  evaluated: "evaluated",
};

export const CASE_STATE_TONE: Record<CaseRunState, string> = {
  pending: "muted",
  ran: "chip-partial",
  evaluated: "chip-pass",
};

export interface CaseListRow {
  case_id: string;
  state: CaseRunState;
  row: CaseRow | null;
}

export function caseList(
  pinned: string[],
  states: Record<string, CaseRunState>,
  report: Report | null,
): CaseListRow[] {
  const rows = new Map((report?.cases ?? []).map((r) => [r.case_id, r]));
  return [...pinned].sort().map((case_id) => ({
    case_id,
    state: states[case_id] ?? "pending",
    row: rows.get(case_id) ?? null,
  }));
}
