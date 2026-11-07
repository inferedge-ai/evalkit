import type { JudgeSettings } from "./types";

export interface JudgeChoice {
  model: string;
  provider: string;
  reasoning_effort: string;
}

export function judgeChoice(defaults: JudgeSettings): JudgeChoice {
  return {
    model: defaults.model,
    provider: defaults.provider ?? "",
    reasoning_effort: defaults.reasoning_effort,
  };
}

// The judge a launch sends: null keeps the server default, so an experiment
// records an override only when the user changed something.
export function judgeOverride(
  defaults: JudgeSettings | null,
  choice: JudgeChoice,
): JudgeSettings | null {
  if (!defaults) {
    const provider = choice.provider.trim();
    const model = choice.model.trim();
    if (!provider || !model) return null;
    return {
      provider,
      model,
      reasoning_effort: choice.reasoning_effort.trim() || "high",
      max_tokens: 32768,
    };
  }
  const picked = {
    model: choice.model.trim() || defaults.model,
    provider: choice.provider.trim() || defaults.provider || "",
    reasoning_effort:
      choice.reasoning_effort.trim() || defaults.reasoning_effort,
  };
  const same =
    picked.model === defaults.model &&
    picked.provider === defaults.provider &&
    picked.reasoning_effort === defaults.reasoning_effort;
  return same ? null : { ...defaults, ...picked, base_url: null };
}
