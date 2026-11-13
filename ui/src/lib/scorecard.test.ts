import { describe, expect, it } from "vitest";
import {
  columnShown,
  describeErroredRuns,
  hasColumn,
  rowFailing,
  worstFirstKey,
} from "./scorecard";
import type { CaseRow, Report } from "./types";

function row(over: Partial<CaseRow>): CaseRow {
  return {
    case_id: "c",
    scores: {},
    explanations: {},
    gauges: {},
    errors: {},
    run_errors: [],
    errored_runs: 0,
    repetitions: 1,
    overall: null,
    baseline_overall: null,
    delta: null,
    ...over,
  };
}

const errored = row({ case_id: "a", errors: { presentation_judge: "403" } });
const scored = row({ case_id: "b", scores: { presentation_judge: 0.9 } });

describe("scorecard errors", () => {
  it("keeps a column whose only entries are errors", () => {
    const report = { cases: [errored] } as unknown as Report;
    expect(columnShown(report, "presentation_judge")).toBe(true);
    expect(columnShown(report, "answer_rubric_score")).toBe(false);
  });

  it("counts an errored evaluator as failing", () => {
    expect(rowFailing(errored)).toBe(true);
    expect(rowFailing(scored)).toBe(false);
  });

  it("fails and labels a row by its errored runs, not by error text", () => {
    const silent = row({ errored_runs: 1 });
    const noted = row({ run_errors: ["non-fatal"], scores: { coverage: 0.9 } });
    expect(rowFailing(silent)).toBe(true);
    expect(describeErroredRuns(silent)).toBe("errored");
    expect(rowFailing(noted)).toBe(false);
    expect(describeErroredRuns(noted)).toBeNull();
    expect(describeErroredRuns(row({ errored_runs: 1, repetitions: 3 }))).toBe(
      "partly errored",
    );
  });

  it("keeps errored rows when focusing an evaluator and sorts them first", () => {
    expect(hasColumn(errored, "presentation_judge")).toBe(true);
    expect(hasColumn(row({}), "presentation_judge")).toBe(false);
    const sorted = [scored, errored].sort(
      (x, y) =>
        worstFirstKey(x, "presentation_judge") -
        worstFirstKey(y, "presentation_judge"),
    );
    expect(sorted.map((r) => r.case_id)).toEqual(["a", "b"]);
  });
});
