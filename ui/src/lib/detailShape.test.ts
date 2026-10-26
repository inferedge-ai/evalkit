import { describe, expect, it } from "vitest";
import {
  detailShape,
  isScalar,
  looksLikeRate,
  nonEmptyKeys,
  unionKeys,
} from "./detailShape";
import { groupEvaluators, groupForConfig } from "./evaluatorGroups";

describe("detailShape", () => {
  it("calls a number a scalar so it never reaches a code editor", () => {
    expect(detailShape(1)).toBe("scalar");
    expect(detailShape(0.85)).toBe("scalar");
    expect(detailShape(true)).toBe("scalar");
    expect(detailShape(null)).toBe("scalar");
    expect(detailShape("pass")).toBe("scalar");
  });

  it("treats a multi-line string as prose rather than a value", () => {
    expect(isScalar("one\ntwo")).toBe(false);
    expect(detailShape("one\ntwo")).toBe("yaml");
  });

  it("recognises a flat map of scalars", () => {
    // The real `citation` block from an answer-rubric run.
    expect(
      detailShape({
        coverage: null,
        accuracy: null,
        rate: null,
        satisfied: 0,
        incorrect: 0,
        absent: 0,
        eligible: 0,
        attempted: 0,
      }),
    ).toBe("scalar-map");
  });

  it("recognises a map of same-shaped maps as a table", () => {
    // The real `by_type` block: one row per rubric type.
    expect(
      detailShape({
        factual_retrieval: { coverage: 1, satisfied: 13, total: 13 },
        derived_computation: { coverage: 1, satisfied: 1, total: 1 },
        analysis_interpretation: { coverage: 1, satisfied: 1, total: 1 },
      }),
    ).toBe("matrix");
  });

  it("does not call a ragged map of maps a table", () => {
    expect(
      detailShape({
        a: { coverage: 1, satisfied: 2 },
        b: { coverage: 1, satisfied: 2, extra: 3 },
      }),
    ).toBe("yaml");
  });

  it("recognises a list of same-shaped objects", () => {
    expect(detailShape([{ a: 1 }, { a: 2 }])).toBe("object-list");
    expect(detailShape([{ a: 1 }, { b: 2 }])).toBe("yaml");
  });

  it("falls back to yaml for a mixed map", () => {
    expect(detailShape({ a: 1, b: { c: 2 } })).toBe("yaml");
  });
});

describe("columns", () => {
  it("keeps first-appearance order across rows", () => {
    expect(unionKeys([{ b: 1 }, { a: 2, b: 3 }])).toEqual(["b", "a"]);
  });

  it("drops a column no row fills", () => {
    // `source` is null on every rubric item unless the rubric asks for one.
    const rows = [
      { index: 1, stated: "x", source: null },
      { index: 2, stated: "y", source: null },
    ];
    expect(nonEmptyKeys(rows)).toEqual(["index", "stated"]);
  });

  it("keeps a column one row fills", () => {
    const rows = [
      { index: 1, source: null },
      { index: 2, source: "10-K p4" },
    ];
    expect(nonEmptyKeys(rows)).toEqual(["index", "source"]);
  });
});

describe("looksLikeRate", () => {
  it("colours a named rate on the 0-1 scale", () => {
    expect(looksLikeRate("coverage", 0.85)).toBe(true);
    expect(looksLikeRate("accuracy", 1)).toBe(true);
  });

  it("leaves a count alone even when it is 0 or 1", () => {
    // `satisfied: 1` is one rubric item, not a 100% rate.
    expect(looksLikeRate("satisfied", 1)).toBe(false);
    expect(looksLikeRate("total", 0)).toBe(false);
  });

  it("ignores a rate-named value outside 0-1", () => {
    expect(looksLikeRate("coverage", 13)).toBe(false);
  });
});

describe("groupEvaluators", () => {
  it("gathers a published family under its shared prefix", () => {
    const groups = groupEvaluators([
      "answer_rubric_score",
      "answer_rubric_coverage",
      "answer_rubric_accuracy",
      "answer_rubric_factual_retrieval",
    ]);
    expect(groups).toHaveLength(1);
    expect(groups[0].title).toBe("answer_rubric");
    expect(groups[0].names).toContain("answer_rubric_score");
  });

  it("leaves a lone evaluator under its own full name", () => {
    // Titling this group "max" would be worse than not grouping it.
    const groups = groupEvaluators(["max_tool_calls", "schema_check"]);
    expect(groups.map((g) => g.title).sort()).toEqual([
      "max_tool_calls",
      "schema_check",
    ]);
  });

  it("groups the claims families separately", () => {
    const groups = groupEvaluators([
      "fact_claims",
      "fact_coverage",
      "opinion_claims",
      "opinion_coverage",
    ]);
    expect(groups.map((g) => g.title).sort()).toEqual(["fact", "opinion"]);
  });

  it("orders a group by the framework's published order", () => {
    const groups = groupEvaluators([
      "answer_rubric_accuracy",
      "answer_rubric_score",
      "answer_rubric_coverage",
    ]);
    expect(groups[0].names[0]).toBe("answer_rubric_score");
  });

  it("groups the buckets together", () => {
    const groups = groupEvaluators(["bucket_efficiency", "bucket_trajectory"]);
    expect(groups.map((g) => g.title)).toEqual(["bucket"]);
  });

  it("keeps two unrelated tool_* evaluators apart", () => {
    // Both start with "tool" but they are different evaluators — a single
    // "tool" card would claim they are one judgement.
    const groups = groupEvaluators([
      "tool_call_efficiency",
      "tool_trajectory_match",
    ]);
    expect(groups.map((g) => g.title).sort()).toEqual([
      "tool_call_efficiency",
      "tool_trajectory_match",
    ]);
  });

  it("matches the real mf-screener score set to six cards", () => {
    const groups = groupEvaluators([
      "answer_rubric_accuracy",
      "answer_rubric_coverage",
      "answer_rubric_derived_computation",
      "answer_rubric_factual_retrieval",
      "answer_rubric_analysis_interpretation",
      "answer_rubric_score",
      "bucket_efficiency",
      "bucket_trajectory",
      "max_tool_calls",
      "required_tools_called",
      "schema_check",
      "tool_call_efficiency",
    ]);
    expect(groups.map((g) => g.title).sort()).toEqual([
      "answer_rubric",
      "bucket",
      "max_tool_calls",
      "required_tools_called",
      "schema_check",
      "tool_call_efficiency",
    ]);
  });
});

describe("groupForConfig", () => {
  const groups = groupEvaluators([
    "answer_rubric_score",
    "answer_rubric_coverage",
    "tool_call_efficiency",
    "tool_trajectory_match",
    "llm_judge",
  ]);

  it("joins a plural config key to the group its scores form", () => {
    // The config is `answer_rubrics`; the scores are `answer_rubric_*`.
    expect(groupForConfig("answer_rubrics", groups)?.title).toBe(
      "answer_rubric",
    );
  });

  it("picks the group sharing the most of the config's name", () => {
    expect(groupForConfig("tool_trajectory_match", groups)?.title).toBe(
      "tool_trajectory_match",
    );
  });

  it("matches an exactly-named evaluator", () => {
    expect(groupForConfig("llm_judge", groups)?.title).toBe("llm_judge");
  });

  it("returns nothing for a config whose evaluator never ran", () => {
    expect(groupForConfig("presentation_judge", groups)).toBeUndefined();
  });
});
