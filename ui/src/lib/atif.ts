// Maps an ATIF trajectory document into the viewer's render model: one row per
// step, tool calls paired to their observation results by source_call_id, and
// one sub-tab per embedded subagent trajectory.

import type {
  AtifContentPart,
  AtifObservationResult,
  AtifStep,
  AtifSubagentRef,
  AtifTrajectory,
} from "./types";

export interface ToolCallRow {
  id: string;
  name: string;
  args: Record<string, unknown>;
  result: string | null;
  isError: boolean;
  subagents: SubagentLink[];
}

export interface SubagentLink {
  name: string;
  tab: string | null;
}

export interface StepRow {
  stepId: number;
  source: "user" | "agent" | "system";
  timestamp: string | null;
  text: string;
  thinking: string | null;
  toolCalls: ToolCallRow[];
  tokens: {
    prompt: number | null;
    completion: number | null;
    cached: number | null;
  } | null;
  costUsd: number | null;
  model: string | null;
}

export interface TrajectoryTab {
  id: string;
  label: string;
  modelName: string | null;
  steps: StepRow[];
}

export interface TrajectoryModel {
  schemaVersion: string;
  agentName: string | null;
  modelName: string | null;
  totals: {
    prompt: number | null;
    completion: number | null;
    cached: number | null;
    costUsd: number | null;
    steps: number;
    toolCalls: number;
    erroredToolCalls: number;
  };
  tabs: TrajectoryTab[];
}

export interface ErrorEntry {
  stepId: number;
  callId: string;
  tool: string;
}

export function errorEntries(steps: StepRow[]): ErrorEntry[] {
  return steps.flatMap((step) =>
    step.toolCalls
      .filter((c) => c.isError)
      .map((c) => ({ stepId: step.stepId, callId: c.id, tool: c.name })),
  );
}

export function messageText(
  message: string | AtifContentPart[] | null | undefined,
): string {
  if (message === null || message === undefined) return "";
  if (typeof message === "string") return message;
  return message
    .filter((part) => part.type === "text" && part.text)
    .map((part) => part.text)
    .join(" ");
}

function resultText(result: AtifObservationResult): string {
  if (result.content === null || result.content === undefined) return "";
  if (typeof result.content === "string") return result.content;
  return result.content
    .filter((part) => part.type === "text" && part.text)
    .map((part) => part.text)
    .join(" ");
}

export function isErrorResult(result: AtifObservationResult): boolean {
  return result.extra?.is_error === true;
}

type ResolveSubagent = (ref: AtifSubagentRef) => SubagentLink | null;

function stepRow(step: AtifStep, resolve: ResolveSubagent): StepRow {
  const resultsByCall = new Map<string, AtifObservationResult>();
  for (const result of step.observation?.results ?? []) {
    if (result.source_call_id) resultsByCall.set(result.source_call_id, result);
  }
  const toolCalls: ToolCallRow[] = (step.tool_calls ?? []).map((call) => {
    const result = resultsByCall.get(call.tool_call_id);
    return {
      id: call.tool_call_id,
      name: call.function_name,
      args: call.arguments,
      result: result ? resultText(result) : null,
      isError: result ? isErrorResult(result) : false,
      subagents: (result?.subagent_trajectory_ref ?? []).flatMap((ref) => {
        const link = resolve(ref);
        return link ? [link] : [];
      }),
    };
  });
  const metrics = step.metrics;
  return {
    stepId: step.step_id,
    source: step.source,
    timestamp: step.timestamp ?? null,
    text: messageText(step.message),
    thinking: step.reasoning_content ?? null,
    toolCalls,
    tokens: metrics
      ? {
          prompt: metrics.prompt_tokens ?? null,
          completion: metrics.completion_tokens ?? null,
          cached: metrics.cached_tokens ?? null,
        }
      : null,
    costUsd: metrics?.cost_usd ?? null,
    model: step.model_name ?? null,
  };
}

export function toModel(doc: AtifTrajectory): TrajectoryModel {
  const subagents = (doc.subagent_trajectories ?? []).map((sub, i) => ({
    doc: sub,
    id: sub.trajectory_id ?? `subagent-${i + 1}`,
    label: sub.agent?.name ?? sub.trajectory_id ?? `subagent ${i + 1}`,
  }));
  const resolve: ResolveSubagent = (ref) => {
    const match = subagents.find(
      (sub) =>
        (ref.trajectory_id && sub.doc.trajectory_id === ref.trajectory_id) ||
        (ref.session_id && sub.doc.session_id === ref.session_id),
    );
    if (match) return { name: match.label, tab: match.id };
    const name = ref.trajectory_id ?? ref.session_id ?? ref.trajectory_path;
    return name ? { name, tab: null } : null;
  };
  const tabs: TrajectoryTab[] = [
    {
      id: "main",
      label: doc.agent?.name ?? "main",
      modelName: doc.agent?.model_name ?? null,
      steps: doc.steps.map((step) => stepRow(step, resolve)),
    },
    ...subagents.map((sub) => ({
      id: sub.id,
      label: sub.label,
      modelName: sub.doc.agent?.model_name ?? null,
      steps: sub.doc.steps.map((step) => stepRow(step, resolve)),
    })),
  ];

  const allSteps = tabs.flatMap((tab) => tab.steps);
  const allCalls = allSteps.flatMap((step) => step.toolCalls);
  const summed = (read: (s: StepRow) => number | null): number | null => {
    let total: number | null = null;
    for (const step of allSteps) {
      const value = step.tokens ? read(step) : null;
      if (value !== null) total = (total ?? 0) + value;
    }
    return total;
  };
  let summedCost: number | null = null;
  for (const step of allSteps) {
    if (step.costUsd !== null) summedCost = (summedCost ?? 0) + step.costUsd;
  }
  const final = doc.final_metrics;
  return {
    schemaVersion: doc.schema_version,
    agentName: doc.agent?.name ?? null,
    modelName: doc.agent?.model_name ?? null,
    totals: {
      prompt: final?.total_prompt_tokens ?? summed((s) => s.tokens!.prompt),
      completion:
        final?.total_completion_tokens ?? summed((s) => s.tokens!.completion),
      cached: final?.total_cached_tokens ?? summed((s) => s.tokens!.cached),
      costUsd: final?.total_cost_usd ?? summedCost,
      steps: doc.steps.length,
      toolCalls: allCalls.length,
      erroredToolCalls: allCalls.filter((c) => c.isError).length,
    },
    tabs,
  };
}

export function trajectoryBounds(doc: AtifTrajectory): {
  start: string | null;
  end: string | null;
} {
  const stamps = [doc, ...(doc.subagent_trajectories ?? [])]
    .flatMap((t) => t.steps)
    .flatMap((step) => (step.timestamp ? [step.timestamp] : []))
    .filter((ts) => !isNaN(new Date(ts).getTime()))
    .sort((a, b) => new Date(a).getTime() - new Date(b).getTime());
  return {
    start: stamps[0] ?? null,
    end: stamps.length > 1 ? stamps.at(-1)! : null,
  };
}
