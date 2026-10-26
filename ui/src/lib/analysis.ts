// Cost and timing rollups computed client-side from the ATIF trajectory —
// evalkit has no server-side analysis endpoint, so the trajectory is the
// single source for both views.

import type { StepRow, TrajectoryModel } from "./atif";

export interface CostRollupRow {
  key: string;
  calls: number;
  input: number;
  output: number;
  cache_read: number;
  cost: number;
  unpriced: boolean;
}

export interface ToolRollupRow {
  key: string;
  calls: number;
  errors: number;
}

export interface CostAnalysis {
  summary: {
    cost: number | null;
    calls: number;
    steps: number;
    input: number;
    output: number;
    cache_read: number;
    models: string[];
    unpriced_models: string[];
  };
  by_model: CostRollupRow[];
  by_agent: CostRollupRow[];
  tool_calls: ToolRollupRow[];
}

function isLlmCall(step: StepRow): boolean {
  return (
    step.tokens !== null &&
    (step.tokens.prompt !== null ||
      step.tokens.completion !== null ||
      step.costUsd !== null)
  );
}

function rollup(steps: { step: StepRow; key: string }[]): CostRollupRow[] {
  const rows = new Map<string, CostRollupRow>();
  for (const { step, key } of steps) {
    if (!isLlmCall(step)) continue;
    const row = rows.get(key) ?? {
      key,
      calls: 0,
      input: 0,
      output: 0,
      cache_read: 0,
      cost: 0,
      unpriced: false,
    };
    row.calls += 1;
    row.input += step.tokens?.prompt ?? 0;
    row.output += step.tokens?.completion ?? 0;
    row.cache_read += step.tokens?.cached ?? 0;
    if (step.costUsd === null) row.unpriced = true;
    else row.cost += step.costUsd;
    rows.set(key, row);
  }
  return [...rows.values()].sort(
    (a, b) => b.cost - a.cost || b.calls - a.calls,
  );
}

export function costAnalysis(model: TrajectoryModel): CostAnalysis {
  const keyed = model.tabs.flatMap((tab) =>
    tab.steps.map((step) => ({
      step,
      tab: tab.label,
      tabModel: tab.modelName,
    })),
  );
  const byModel = rollup(
    keyed.map(({ step, tabModel }) => ({
      step,
      key: step.model ?? tabModel ?? "unknown",
    })),
  );
  const byAgent = rollup(keyed.map(({ step, tab }) => ({ step, key: tab })));

  const tools = new Map<string, ToolRollupRow>();
  for (const { step } of keyed) {
    for (const call of step.toolCalls) {
      const row = tools.get(call.name) ?? {
        key: call.name,
        calls: 0,
        errors: 0,
      };
      row.calls += 1;
      if (call.isError) row.errors += 1;
      tools.set(call.name, row);
    }
  }

  return {
    summary: {
      cost: model.totals.costUsd,
      calls: keyed.filter(({ step }) => isLlmCall(step)).length,
      steps: keyed.length,
      input: model.totals.prompt ?? 0,
      output: model.totals.completion ?? 0,
      cache_read: model.totals.cached ?? 0,
      models: [...new Set(byModel.map((r) => r.key))],
      unpriced_models: byModel.filter((r) => r.unpriced).map((r) => r.key),
    },
    by_model: byModel,
    by_agent: byAgent,
    tool_calls: [...tools.values()].sort((a, b) => b.calls - a.calls),
  };
}

export interface TimingRow {
  step: number;
  agent: string;
  source: string;
  tools: string[];
  start_ms: number;
  duration_ms: number | null;
}

export interface TimingAnalysis {
  wall_ms: number | null;
  steps: number;
  timed_steps: number;
  rows: TimingRow[];
  slowest: TimingRow[];
}

const SLOWEST = 10;

function parseTs(iso: string | null): number | null {
  if (!iso) return null;
  const ms = Date.parse(iso);
  return Number.isNaN(ms) ? null : ms;
}

// A step's duration is the gap to the next timestamped step in the same
// trajectory — it covers the model call plus any tool calls the step made.
// The last step of each trajectory has no successor, so no duration.
export function timingAnalysis(model: TrajectoryModel): TimingAnalysis {
  const rows: TimingRow[] = [];
  let origin: number | null = null;
  for (const tab of model.tabs) {
    const stamps = tab.steps.map((s) => parseTs(s.timestamp));
    for (const ms of stamps) {
      if (ms !== null && (origin === null || ms < origin)) origin = ms;
    }
    for (let i = 0; i < tab.steps.length; i++) {
      const start = stamps[i];
      if (start === null) continue;
      const next = stamps.slice(i + 1).find((ms) => ms !== null) ?? null;
      rows.push({
        step: tab.steps[i].stepId,
        agent: tab.label,
        source: tab.steps[i].source,
        tools: tab.steps[i].toolCalls.map((c) => c.name),
        start_ms: start,
        duration_ms: next !== null ? Math.max(0, next - start) : null,
      });
    }
  }
  rows.sort((a, b) => a.start_ms - b.start_ms);
  if (origin !== null) {
    for (const row of rows) row.start_ms -= origin;
  }
  const timed = rows.filter((r) => r.duration_ms !== null);
  const last = rows[rows.length - 1];
  return {
    wall_ms: last ? last.start_ms + (last.duration_ms ?? 0) : null,
    steps: model.tabs.reduce((n, tab) => n + tab.steps.length, 0),
    timed_steps: timed.length,
    rows,
    slowest: [...timed]
      .sort((a, b) => (b.duration_ms ?? 0) - (a.duration_ms ?? 0))
      .slice(0, SLOWEST),
  };
}
