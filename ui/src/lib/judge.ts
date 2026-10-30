import type { JudgeSettings } from "./types";

export interface JudgeChoice {
  model: string;
  base_url: string;
  reasoning_effort: string;
}

export function judgeChoice(defaults: JudgeSettings): JudgeChoice {
  return {
    model: defaults.model,
    base_url: defaults.base_url,
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
    const base_url = choice.base_url.trim();
    const model = choice.model.trim();
    if (!base_url || !model) return null;
    return {
      base_url,
      model,
      reasoning_effort: choice.reasoning_effort.trim() || "high",
      max_tokens: 32768,
      concurrency: 16,
    };
  }
  const picked = {
    model: choice.model.trim() || defaults.model,
    base_url: choice.base_url.trim() || defaults.base_url,
    reasoning_effort:
      choice.reasoning_effort.trim() || defaults.reasoning_effort,
  };
  const same =
    picked.model === defaults.model &&
    picked.base_url === defaults.base_url &&
    picked.reasoning_effort === defaults.reasoning_effort;
  return same ? null : { ...defaults, ...picked };
}
