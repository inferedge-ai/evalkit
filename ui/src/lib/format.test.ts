import { afterEach, describe, expect, it, vi } from "vitest";
import {
  answerText,
  splitTags,
  experimentLabel,
  experimentTag,
  fmtDelta,
  fmtScore,
  fmtWhen,
  elapsedMs,
  formatBytes,
  formatElapsed,
  formatMs,
  formatTokens,
  formatUsd,
  normalizeMathDelimiters,
  orderEvaluatorNames,
  outputText,
  questionText,
  severity,
  splitLabel,
  verdictKind,
  verdictWord,
} from "./format";

describe("severity", () => {
  it("buckets scores at 0.8 / 0.5", () => {
    expect(severity(1)).toBe("pass");
    expect(severity(0.8)).toBe("pass");
    expect(severity(0.79)).toBe("partial");
    expect(severity(0.5)).toBe("partial");
    expect(severity(0.49)).toBe("fail");
    expect(severity(null)).toBe("none");
  });
});

describe("formatters", () => {
  it("formats scores, deltas, usd, tokens, bytes, ms", () => {
    expect(fmtScore(0.5)).toBe("0.50");
    expect(fmtScore(null)).toBe("—");
    expect(fmtDelta(0.5)).toBe("+0.50");
    expect(fmtDelta(-0.25)).toBe("−0.25");
    expect(formatUsd(0.1234)).toBe("$0.1234");
    expect(formatUsd(12.3)).toBe("$12.30");
    expect(formatTokens(1_500_000)).toBe("1.5M");
    expect(formatBytes(2048)).toBe("2.0 kB");
    expect(formatMs(65_000)).toBe("1.1m");
  });
});

describe("verdicts", () => {
  it("maps vocabulary onto canonical kinds", () => {
    expect(verdictKind("satisfied")).toBe("satisfied");
    expect(verdictKind("hit")).toBe("satisfied");
    expect(verdictKind(true)).toBe("satisfied");
    expect(verdictKind("partial")).toBe("partial");
    expect(verdictKind("weird")).toBe("other");
    expect(verdictWord("hit")).toBe("hit");
    expect(verdictWord("satisfied")).toBeNull();
  });

  it("splits gaps from wrong answers", () => {
    expect(verdictKind("missing")).toBe("absent");
    expect(verdictKind("absent")).toBe("absent");
    expect(verdictKind("omitted")).toBe("absent");
    expect(verdictKind("unsatisfied")).toBe("incorrect");
    expect(verdictWord("unsatisfied")).toBe("unsatisfied");
    expect(verdictKind("incorrect")).toBe("incorrect");
    expect(verdictKind("violated")).toBe("incorrect");
    expect(verdictKind(false)).toBe("incorrect");
  });
});

describe("text extraction", () => {
  it("pulls response/question strings out of objects", () => {
    expect(answerText({ response: "hello" })).toBe("hello");
    expect(answerText("plain")).toBe("plain");
    expect(questionText({ question: "q?" })).toBe("q?");
  });
});

describe("experimentTag", () => {
  it("labels harness and model", () => {
    expect(
      experimentTag({ agent: "myagent", model: "vertex_ai/gemini-3.6-flash" }),
    ).toBe("myagent · gemini-3.6-flash");
    expect(experimentTag({ agent: "support-bot" })).toBe("support-bot");
    expect(experimentTag(undefined)).toBeNull();
  });
});

describe("splitLabel", () => {
  it("reads the split from the experiment selection", () => {
    expect(splitLabel({ selection: { tag: "starbucks" } })).toBe("starbucks");
    expect(splitLabel({ selection: { all: true } })).toBe("all cases");
  });

  it("labels a case-id selection by its cases", () => {
    expect(splitLabel({ selection: { case_ids: ["a", "b", "c"] } })).toBe(
      "3 cases",
    );
    expect(splitLabel({ selection: { case_ids: ["a"] } })).toBe("a");
  });

  it("returns null when there is no selection", () => {
    expect(splitLabel({})).toBeNull();
    expect(splitLabel(undefined)).toBeNull();
    expect(splitLabel({ selection: {} })).toBeNull();
  });
});

describe("experimentLabel", () => {
  it("drops the dataset prefix and splits the run stamp off the variant", () => {
    expect(
      experimentLabel(
        "nightly-chat-akshat-myagent-working:akshat-develop-799f8284-20260819-105501",
        "nightly-chat-akshat",
      ),
    ).toEqual({
      variant: "myagent-working",
      stamp: "akshat-develop-799f8284-20260819-105501",
    });
  });

  it("keeps the whole name when the dataset prefix does not match", () => {
    expect(experimentLabel("other-run:stamp", "nightly-chat")).toEqual({
      variant: "other-run",
      stamp: "stamp",
    });
  });

  it("handles a name with no stamp and a missing name", () => {
    expect(experimentLabel("bare-name", "bare")).toEqual({
      variant: "name",
      stamp: null,
    });
    expect(experimentLabel(null, "d")).toEqual({ variant: "", stamp: null });
  });
});

describe("orderEvaluatorNames", () => {
  it("groups the answer-rubric rates ahead of the per-type breakdown", () => {
    expect(
      orderEvaluatorNames([
        "answer_rubric_analysis_interpretation",
        "answer_rubric_citation",
        "answer_rubric_accuracy",
        "answer_rubric_factual_retrieval",
        "answer_rubric_score",
        "answer_rubric_coverage",
      ]),
    ).toEqual([
      "answer_rubric_score",
      "answer_rubric_coverage",
      "answer_rubric_accuracy",
      "answer_rubric_citation",
      "answer_rubric_factual_retrieval",
      "answer_rubric_analysis_interpretation",
    ]);
  });

  it("keeps every other evaluator alphabetical, after the rubric scores", () => {
    expect(
      orderEvaluatorNames([
        "trajectory_rubric_score",
        "answer_rubric_score",
        "max_tool_calls",
      ]),
    ).toEqual([
      "answer_rubric_score",
      "max_tool_calls",
      "trajectory_rubric_score",
    ]);
  });
});

describe("fmtWhen", () => {
  afterEach(() => vi.unstubAllEnvs());

  it("renders in the browser's timezone with its short label", () => {
    vi.stubEnv("TZ", "Asia/Kolkata");
    expect(fmtWhen("2026-09-02T13:12:00Z")).toBe("02 Sept 2026, 18:42 IST");
    vi.stubEnv("TZ", "America/New_York");
    expect(fmtWhen("2026-09-02T13:12:00Z")).toBe("02 Sept 2026, 09:12 EDT");
  });

  it("returns null for missing or invalid input", () => {
    expect(fmtWhen(null)).toBe(null);
    expect(fmtWhen("not a date")).toBe(null);
  });
});

describe("normalizeMathDelimiters", () => {
  it("translates bracket delimiters to dollar math", () => {
    expect(normalizeMathDelimiters("so \\(17 \\times 23 = 391\\) holds")).toBe(
      "so $17 \\times 23 = 391$ holds",
    );
    expect(
      normalizeMathDelimiters(
        "\\[ CAGR = \\left(\\frac{21,514}{17,133}\\right)^{1/4} - 1 = 5.86\\% \\]",
      ),
    ).toBe(
      "$$ CAGR = \\left(\\frac{21,514}{17,133}\\right)^{1/4} - 1 = 5.86\\% $$",
    );
  });

  it("leaves code spans and fences untouched", () => {
    expect(normalizeMathDelimiters("`\\(not math\\)`")).toBe(
      "`\\(not math\\)`",
    );
    expect(normalizeMathDelimiters("```\n\\[block\\]\n```")).toBe(
      "```\n\\[block\\]\n```",
    );
  });

  it("escapes raw dollar signs so currency is not read as math", () => {
    expect(normalizeMathDelimiters("AWS made $42.2bn, up from $30bn.")).toBe(
      "AWS made \\$42.2bn, up from \\$30bn.",
    );
    expect(normalizeMathDelimiters("$a+b$ and $$c$$")).toBe(
      "\\$a+b\\$ and \\$\\$c\\$\\$",
    );
  });

  it("escapes currency while still converting real latex", () => {
    expect(normalizeMathDelimiters("cost \\(x\\) was $5")).toBe(
      "cost $x$ was \\$5",
    );
  });

  it("leaves plain prose unchanged", () => {
    expect(normalizeMathDelimiters("plain (parens) [brackets]")).toBe(
      "plain (parens) [brackets]",
    );
  });
});

describe("outputText", () => {
  it("pretty-prints a structured output", () => {
    expect(outputText({ label: "EVENT" })).toBe('{\n  "label": "EVENT"\n}');
  });
  it("is null without an output", () => {
    expect(outputText(null)).toBeNull();
    expect(outputText(undefined)).toBeNull();
  });
});

describe("splitTags", () => {
  it("adds overlay tags to the case's own tags once", () => {
    expect(
      splitTags({
        tags: ["finance"],
        overlays: { "client-cases": { tags: ["client-priority", "finance"] } },
      }),
    ).toEqual(["finance", "client-priority"]);
  });

  it("handles a case with no tags or overlays", () => {
    expect(splitTags({})).toEqual([]);
  });
});

describe("elapsed time", () => {
  it("measures start to end, and nothing when either is missing", () => {
    expect(elapsedMs("2026-10-14T10:00:00Z", "2026-10-14T10:04:12Z")).toBe(
      252_000,
    );
    expect(elapsedMs("2026-10-14T10:00:00Z", null)).toBe(null);
    expect(elapsedMs("2026-10-14T10:00:00Z", "2026-10-14T09:00:00Z")).toBe(
      null,
    );
    expect(elapsedMs("garbage", "2026-10-14T09:00:00Z")).toBe(null);
  });

  it("formats seconds, minutes and hours", () => {
    expect(formatElapsed(12_400)).toBe("12s");
    expect(formatElapsed(252_000)).toBe("4m12s");
    expect(formatElapsed(3_900_000)).toBe("1h5m");
  });
});
