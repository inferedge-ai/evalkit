import { describe, expect, it } from "vitest";
import {
  isRubricSummary,
  joinRubricItems,
  rubricRows,
  sliceCounts,
} from "./rubricSummary";

const detail = {
  coverage: 0.95,
  accuracy: 0.9,
  recall: 0.85,
  satisfied: 17,
  incorrect: 2,
  absent: 1,
  total: 20,
  attempted: 19,
  by_type: {
    factual_retrieval: {
      coverage: 1,
      accuracy: 0.9,
      recall: 0.9,
      satisfied: 9,
      incorrect: 1,
      absent: 0,
      total: 10,
      attempted: 10,
    },
    derived_computation: {
      coverage: 0.9,
      accuracy: 0.89,
      recall: 0.8,
      satisfied: 8,
      incorrect: 1,
      absent: 1,
      total: 10,
      attempted: 9,
    },
  },
  citation: {
    coverage: 0.8,
    accuracy: 0.75,
    rate: 0.6,
    satisfied: 6,
    incorrect: 2,
    absent: 2,
    eligible: 10,
    attempted: 8,
  },
};

describe("isRubricSummary", () => {
  it("recognises the rubric judge's count payload", () => {
    expect(isRubricSummary(detail)).toBe(true);
    expect(isRubricSummary({ ...detail, by_type: {}, citation: {} })).toBe(
      false,
    );
    expect(isRubricSummary({ items: [] })).toBe(false);
    expect(isRubricSummary({ coverage: 0.5, total: "20", satisfied: 1 })).toBe(
      false,
    );
  });
});

describe("rubricRows", () => {
  const rows = rubricRows(detail);
  const byLabel = Object.fromEntries(rows.map((r) => [r.label, r.cells]));

  it("builds one row per slice: all, each type, citation", () => {
    expect(rows.map((r) => r.label)).toEqual([
      "all",
      "factual_retrieval",
      "derived_computation",
      "citation",
    ]);
  });

  it("scores overall and type rows on recall, three rates wide", () => {
    expect(byLabel.all).toEqual({ score: 0.85, coverage: 0.95, accuracy: 0.9 });
    expect(byLabel.factual_retrieval.score).toBe(0.9);
    expect(byLabel.derived_computation.coverage).toBe(0.9);
  });

  it("marks type rows as the drill-down and the rest as always-on", () => {
    expect(rows.map((r) => r.kind)).toEqual([
      "all",
      "type",
      "type",
      "citation",
    ]);
  });

  it("scores the citation row on rate and counts eligible as its total", () => {
    expect(byLabel.citation.score).toBe(0.6);
    const citation = rows.find((r) => r.label === "citation");
    expect(citation?.counts).toBe(
      "6/10 satisfied · 2 incorrect · 2 absent · 8 attempted",
    );
  });

  it("folds the counts into one hover line per row", () => {
    expect(rows[0].counts).toBe(
      "17/20 satisfied · 2 incorrect · 1 absent · 19 attempted",
    );
    expect(rows.find((r) => r.label === "derived_computation")?.counts).toBe(
      "8/10 satisfied · 1 incorrect · 1 absent · 9 attempted",
    );
  });

  it("omits the citation row when the judge reported none", () => {
    const rows = rubricRows({ ...detail, citation: undefined });
    expect(rows.map((r) => r.label)).toEqual([
      "all",
      "factual_retrieval",
      "derived_computation",
    ]);
  });
});

describe("sliceCounts", () => {
  it("returns null when the slice carries no counts", () => {
    expect(sliceCounts({ coverage: 1 }, 3)).toBe(null);
  });

  it("drops the denominator when total is not a number", () => {
    expect(sliceCounts({ satisfied: 4, absent: 1 }, undefined)).toBe(
      "4 satisfied · 1 absent",
    );
  });
});

describe("joinRubricItems", () => {
  const cfg = {
    rubrics: [
      { text: "States FY24 revenue", type: "factual_retrieval" },
      { text: "Computes the CAGR", type: "derived_computation" },
    ],
  };
  const legacy = [
    { index: 1, verdict: "satisfied", stated: "$1.2B", source: "10-K" },
    { index: 2, verdict: "missing", stated: null, source: null },
  ];

  it("joins rubric text and type onto legacy verdicts by index", () => {
    const joined = joinRubricItems(legacy, cfg);
    expect(joined[0].rubric).toBe("States FY24 revenue");
    expect(joined[0].type).toBe("factual_retrieval");
    expect(joined[1].rubric).toBe("Computes the CAGR");
    expect(joined[1].type).toBe("derived_computation");
  });

  it("prefers keys the explanation already carries", () => {
    const carried = [
      {
        index: 1,
        verdict: "satisfied",
        rubric: "From the server",
        type: "citation",
      },
    ];
    const joined = joinRubricItems(carried, cfg);
    expect(joined[0].rubric).toBe("From the server");
    expect(joined[0].type).toBe("citation");
  });

  it("falls back to positional order when items carry no index", () => {
    const joined = joinRubricItems(
      [{ verdict: "satisfied" }, { verdict: "partial" }],
      cfg,
    );
    expect(joined[0].rubric).toBe("States FY24 revenue");
    expect(joined[1].rubric).toBe("Computes the CAGR");
  });

  it("finds the rubrics list inside a grouped member-name → config map", () => {
    const grouped = { answer_rubric: cfg, other: { threshold: 1 } };
    const joined = joinRubricItems(legacy, grouped);
    expect(joined[0].rubric).toBe("States FY24 revenue");
  });

  it("handles string rubrics and leaves non-rubric configs untouched", () => {
    const joined = joinRubricItems(legacy, { rubrics: ["Only text"] });
    expect(joined[0].rubric).toBe("Only text");
    expect(joined[0].type).toBeUndefined();
    expect(joined[1].rubric).toBeUndefined();
    expect(joinRubricItems(legacy, { threshold: 3 })).toEqual(legacy);
    expect(joinRubricItems(legacy, undefined)).toEqual(legacy);
  });
});
