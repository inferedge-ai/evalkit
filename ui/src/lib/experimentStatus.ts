import type { AgentInfo, Experiment, ExperimentState, Progress } from "./types";

export const STATUSES: ExperimentState[] = [
  "completed",
  "running",
  "evaluating",
  "failed",
];

export const ACTIVE: ExperimentState[] = ["running", "evaluating"];

export function isActive(status: ExperimentState): boolean {
  return ACTIVE.includes(status);
}

export const STATUS_TONE: Record<string, string> = {
  completed: "chip-pass",
  running: "chip",
  evaluating: "chip",
  failed: "chip-fail",
};

export function progressLabel(progress: Progress): string {
  const { total, ran, evaluated } = progress;
  return `${ran}/${total} ran · ${evaluated}/${total} evaluated`;
}

export function shortProgress(progress: Progress): string {
  return `${progress.evaluated}/${progress.total}`;
}

export function isIncomplete(progress: Progress): boolean {
  return progress.evaluated < progress.total;
}

export const EXPERIMENT_LIST_LIMIT = 200;

export function listCapNote(count: number): string | null {
  return count >= EXPERIMENT_LIST_LIMIT
    ? `Showing the latest ${EXPERIMENT_LIST_LIMIT} experiments; older ones are not listed.`
    : null;
}

type Resumable = Pick<Experiment, "status"> & {
  resumable: boolean;
  pending_runs: boolean;
};

export function canResume(exp: Resumable): boolean {
  return exp.resumable;
}

export function reevaluateStrandsCases(exp: Resumable): boolean {
  return exp.status === "failed" && exp.pending_runs;
}

export function resumeHint(
  exp: Pick<Experiment, "id" | "dataset" | "agent">,
  driver: AgentInfo["driver"],
): string {
  if (driver === "sdk")
    return `re-run from your script: run_experiment(..., resume=${exp.id})`;
  return `evalkit run ${exp.dataset} --agent ${exp.agent} --resume ${exp.id}`;
}
