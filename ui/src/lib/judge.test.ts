import { describe, expect, it } from "vitest";
import { judgeChoice, judgeOverride } from "./judge";
import type { JudgeSettings } from "./types";

const defaults: JudgeSettings = {
  provider: "openai",
  model: "judge-model",
  reasoning_effort: "high",
  max_tokens: 32768,
};

describe("judge override", () => {
  it("prefills from the server default and sends nothing when unchanged", () => {
    expect(judgeChoice(defaults)).toEqual({
      model: "judge-model",
      provider: "openai",
      reasoning_effort: "high",
    });
    expect(judgeOverride(defaults, judgeChoice(defaults))).toBeNull();
  });

  it("without a server default, sends a judge once provider and model are picked", () => {
    const empty = { model: "", provider: "", reasoning_effort: "" };
    expect(judgeOverride(null, empty)).toBeNull();
    expect(judgeOverride(null, { ...empty, provider: "openai" })).toBeNull();
    expect(judgeOverride(null, judgeChoice(defaults))).toEqual(defaults);
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
      base_url: null,
    });
  });
});
