import { describe, expect, it } from "vitest";
import {
  bucketCards,
  classifyCase,
  compareCards,
  compareRows,
  failedChecks,
  gaugeGroup,
  hasScores,
  mean,
  scoreGroups,
  serverMean,
  visibleStat,
} from "./scoreSummary";
import type { CaseRow, EvaluatorStat, Report } from "./types";

function row(over: Partial<CaseRow>): CaseRow {
  return {
    case_id: "c",
    scores: {},
    explanations: {},
    gauges: {},
    errors: {},
    run_errors: [],
    repetitions: 1,
    overall: null,
    baseline_overall: null,
    delta: null,
    ...over,
  };
}

function stat(over: Partial<EvaluatorStat>): EvaluatorStat {
  return {
    name: "e",
    score: null,
    cases: 0,
    errors: 0,
    bucket: null,
    source: "builtin",
    ...over,
  };
}

function report(over: Partial<Report>): Report {
  return {
    experiment_id: "1",
    name: "#1",
    dataset: "d@w",
    metadata: {},
    cases: [],
    evaluators: [],
    buckets: {},
    gauges: [],
    evaluator_columns: [],
    baseline_id: null,
    ...over,
  };
}

const custom = report({
  buckets: { content: null, custom: 0.43 },
  evaluators: [
    stat({ name: "ux_effort", score: 0.77, cases: 14, bucket: "custom" }),
    stat({ name: "ux_goal", score: 0.36, cases: 12, bucket: "custom" }),
    stat({ name: "answer_rubric_coverage", score: 0.5, cases: 14 }),
  ],
  evaluator_columns: ["answer_rubric_coverage", "ux_effort", "ux_goal"],
  cases: [
    row({
      case_id: "a",
      scores: { ux_effort: 1, ux_goal: 0.5 },
      overall: 0.75,
      gauges: { gauge_turns: 4 },
    }),
    row({
      case_id: "b",
      scores: { ux_effort: 0.5, ux_goal: 0 },
      overall: 0.25,
      gauges: { gauge_turns: 2 },
    }),
  ],
  gauges: [
    { name: "gauge_turns", mean: 3, median: 3, count: 2 },
    { name: "gauge_subagents", mean: null, median: null, count: 0 },
  ],
});

describe("mean", () => {
  it("skips nulls and returns null when nothing is left", () => {
    expect(mean([1, null, 0])).toBe(0.5);
    expect(mean([null, undefined])).toBeNull();
  });
});

describe("bucketCards", () => {
  it("drops empty buckets and lists the bucket's members only", () => {
    const cards = bucketCards(custom);
    expect(cards.map((c) => c.bucket)).toEqual(["custom"]);
    expect(cards[0].score).toBe(0.43);
    expect(cards[0].members.map((m) => m.name)).toEqual([
      "ux_effort",
      "ux_goal",
    ]);
  });
});

describe("scoreGroups", () => {
  it("groups bucketed evaluators by bucket and diagnostics after", () => {
    expect(scoreGroups(custom)).toEqual([
      { label: "custom", kind: "score", cols: ["ux_effort", "ux_goal"] },
      {
        label: "answer rubric",
        kind: "score",
        cols: ["answer_rubric_coverage"],
      },
    ]);
  });
});

describe("gaugeGroup", () => {
  it("keeps gauges with at least one value", () => {
    expect(gaugeGroup(custom)).toEqual({
      label: "gauges",
      kind: "gauge",
      cols: ["gauge_turns"],
    });
    expect(gaugeGroup(report({}))).toBeNull();
  });
});

describe("footer stats", () => {
  it("takes the server mean for scores and gauges and averages overall for avg", () => {
    expect(serverMean(custom, "score", "ux_goal")).toBe(0.36);
    expect(serverMean(custom, "gauge", "gauge_turns")).toBe(3);
    expect(serverMean(custom, "avg", "")).toBe(0.5);
  });

  it("averages the visible rows", () => {
    expect(visibleStat([custom.cases[1]], "score", "ux_effort")).toEqual({
      mean: 0.5,
      n: 1,
    });
    expect(visibleStat(custom.cases, "gauge", "gauge_turns")).toEqual({
      mean: 3,
      n: 2,
    });
  });
});

describe("hasScores", () => {
  it("is false when no evaluator has a score", () => {
    expect(hasScores(custom)).toBe(true);
    expect(hasScores(report({ evaluators: [stat({})] }))).toBe(false);
  });
});

describe("compareCards", () => {
  const base = report({
    buckets: { content: 0.6, custom: null },
    evaluators: [
      stat({ name: "answer_rubric_score", score: 0.6, bucket: "content" }),
      stat({ name: "old_judge", score: 0.4, bucket: "content" }),
    ],
  });
  const compare = report({
    buckets: { content: 0.7, custom: 0.43 },
    evaluators: [
      stat({ name: "answer_rubric_score", score: 0.7, bucket: "content" }),
      stat({ name: "ux_effort", score: 0.43, bucket: "custom" }),
    ],
  });

  it("keeps buckets scored on either side and marks one-sided members", () => {
    const cards = compareCards(base, compare);
    expect(cards.map((c) => [c.bucket, c.base, c.compare])).toEqual([
      ["content", 0.6, 0.7],
      ["custom", null, 0.43],
    ]);
    expect(cards[0].members).toEqual([
      { name: "answer_rubric_score", base: 0.6, compare: 0.7, side: "both" },
      { name: "old_judge", base: 0.4, compare: null, side: "base" },
    ]);
    expect(cards[1].members[0].side).toBe("compare");
  });
});

describe("classifyCase", () => {
  it("uses a 0.05 threshold on the Avg delta", () => {
    const at = (b: number | null, c: number | null) =>
      classifyCase(row({ overall: b }), row({ overall: c }));
    expect(at(0.5, 0.55)).toBe("improved");
    expect(at(0.5, 0.45)).toBe("regressed");
    expect(at(0.5, 0.54)).toBe("unchanged");
    expect(at(null, 0.5)).toBe("unscored");
    expect(classifyCase(row({}), undefined)).toBe("only_in_base");
    expect(classifyCase(undefined, row({}))).toBe("only_in_compare");
  });
});

describe("compareRows", () => {
  it("sorts the worst regression first and rows without a delta last", () => {
    const base = report({
      cases: [
        row({ case_id: "a", overall: 0.5 }),
        row({ case_id: "b", overall: 0.9 }),
        row({ case_id: "c", overall: 0.5 }),
      ],
    });
    const compare = report({
      cases: [
        row({ case_id: "a", overall: 0.6 }),
        row({ case_id: "b", overall: 0.3 }),
        row({ case_id: "d", overall: 0.5 }),
      ],
    });
    expect(
      compareRows(base, compare).map((r) => [r.case_id, r.change]),
    ).toEqual([
      ["b", "regressed"],
      ["a", "improved"],
      ["c", "only_in_base"],
      ["d", "only_in_compare"],
    ]);
  });
});

describe("failedChecks", () => {
  it("keeps plain text as one line", () => {
    expect(failedChecks("run took 542s against a cap of 900s")).toEqual({
      lines: ["run took 542s against a cap of 900s"],
      more: 0,
    });
  });

  it("lists the checks that did not pass from a JSON explanation", () => {
    const text = JSON.stringify({
      satisfied: 1,
      verdicts: [
        { rubric: "Retrieves operating income", verdict: "satisfied" },
        { rubric: "States the share count", verdict: "absent" },
      ],
      items: [{ text: "Shows a table", verdict: "missing" }],
    });
    expect(failedChecks(text)).toEqual({
      lines: ["absent: States the share count", "missing: Shows a table"],
      more: 0,
    });
  });

  it("caps the list and counts the rest", () => {
    const verdicts = Array.from({ length: 5 }, (_, i) => ({
      rubric: `r${i}`,
      verdict: "unsatisfied",
    }));
    const out = failedChecks(JSON.stringify({ verdicts }));
    expect(out.lines).toHaveLength(3);
    expect(out.more).toBe(2);
  });

  it("keeps text that only looks like JSON as one line", () => {
    const text = "[low] tool_error (deterministic): 1 of 7 tool calls errored";
    expect(failedChecks(text)).toEqual({ lines: [text], more: 0 });
    expect(failedChecks("{not json")).toEqual({
      lines: ["{not json"],
      more: 0,
    });
  });

  it("shows nothing for JSON without checks", () => {
    expect(failedChecks(JSON.stringify({ coverage: 0.5 }))).toEqual({
      lines: [],
      more: 0,
    });
  });
});
