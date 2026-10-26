import { describe, expect, it } from "vitest";
import { describeTerm, GLOSSARY } from "./glossary";

const SCORE_NAMES = [
  "answer_rubric_score",
  "answer_rubric_coverage",
  "answer_rubric_accuracy",
  "answer_rubric_citation",
  "answer_rubric_factual_retrieval",
  "answer_rubric_derived_computation",
  "answer_rubric_analysis_interpretation",
  "trajectory_rubric_score",
  "trajectory_rubrics",
  "tool_trajectory_match",
  "required_tools_called",
  "presentation_judge",
  "max_response_time",
];

const GAUGE_NAMES = [
  "gauge_duration_seconds",
  "gauge_input_tokens",
  "gauge_output_tokens",
  "gauge_cached_tokens",
  "gauge_agent_cost_usd",
  "gauge_judge_cost_usd",
  "gauge_total_tool_calls",
  "gauge_errored_tool_calls",
  "gauge_subagents",
  "gauge_turns",
];

const BUCKET_NAMES = [
  "content",
  "trajectory",
  "presentation",
  "efficiency",
  "custom",
];

describe("glossary", () => {
  it("covers every builtin score name", () => {
    for (const name of SCORE_NAMES)
      expect(describeTerm(name), name).toBeTruthy();
  });

  it("covers every builtin gauge name", () => {
    for (const name of GAUGE_NAMES)
      expect(describeTerm(name), name).toBeTruthy();
  });

  it("covers every bucket under the bucket: prefix", () => {
    for (const name of BUCKET_NAMES)
      expect(describeTerm(`bucket:${name}`), name).toBeTruthy();
  });

  it("covers the answer_rubric group title and config key", () => {
    expect(describeTerm("answer_rubric")).toBeTruthy();
    expect(describeTerm("answer_rubrics")).toBe(describeTerm("answer_rubric"));
  });

  it("returns undefined for unknown names so custom evaluators get no tooltip", () => {
    expect(describeTerm("my_custom_eval")).toBeUndefined();
    expect(describeTerm("content")).toBeUndefined();
  });

  it("keeps entries one-line and non-empty", () => {
    for (const [name, text] of Object.entries(GLOSSARY)) {
      expect(text.trim().length, name).toBeGreaterThan(0);
      expect(text, name).not.toContain("\n");
    }
  });
});
