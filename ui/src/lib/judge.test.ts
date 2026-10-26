import { describe, expect, it } from "vitest";
import { judgeChoice, judgeOverride } from "./judge";
import type { JudgeSettings } from "./types";

const defaults: JudgeSettings = {
  base_url: "https://llm/v1",
  model: "judge-model",
  reasoning_effort: "high",
  max_tokens: 32768,
  concurrency: 16,
};

describe("judge override", () => {
  it("prefills from the server default and sends nothing when unchanged", () => {
    expect(judgeChoice(defaults)).toEqual({
      model: "judge-model",
      base_url: "https://llm/v1",
      reasoning_effort: "high",
    });
    expect(judgeOverride(defaults, judgeChoice(defaults))).toBeNull();
    expect(judgeOverride(null, judgeChoice(defaults))).toBeNull();
  });

  it("sends the full settings when a field changes", () => {
    const changed = {
      ...judgeChoice(defaults),
      model: "other",
      reasoning_effort: " ",
    };
    expect(judgeOverride(defaults, changed)).toEqual({
      ...defaults,
      model: "other",
    });
  });
});
