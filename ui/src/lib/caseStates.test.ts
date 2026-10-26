import { describe, expect, it } from "vitest";
import { CASE_STATE_LABEL, caseList } from "./caseStates";
import type { CaseRow, Report } from "./types";

const row = (case_id: string): CaseRow => ({
  case_id,
  scores: { answer_rubric_score: 0.5 },
  explanations: {},
  gauges: {},
  errors: {},
  run_errors: [],
  repetitions: 1,
  overall: 0.5,
  baseline_overall: null,
  delta: null,
});

describe("caseList", () => {
  it("lists every pinned case with its state and any scores", () => {
    const report = { cases: [row("b")] } as unknown as Report;
    const list = caseList(
      ["c", "a", "b"],
      { a: "ran", b: "evaluated" },
      report,
    );
    expect(list.map((r) => [r.case_id, r.state, r.row !== null])).toEqual([
      ["a", "ran", false],
      ["b", "evaluated", true],
      ["c", "pending", false],
    ]);
    expect(list.map((r) => CASE_STATE_LABEL[r.state])).toEqual([
      "ran, not evaluated",
      "evaluated",
      "not run",
    ]);
  });

  it("shows pinned cases before any score arrives", () => {
    expect(caseList(["a"], {}, null)).toEqual([
      { case_id: "a", state: "pending", row: null },
    ]);
  });
});
