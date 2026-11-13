import type { BadgeVariant } from "@/components/ui/badge";
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

export const STATUS_TONE: Record<string, BadgeVariant> = {
  completed: "pass",
  running: "info",
  evaluating: "info",
  failed: "fail",
};

export const RUN_STATUS_TONE: Record<string, BadgeVariant> = {
  completed: "pass",
  queued: "info",
  running: "info",
  failed: "fail",
  timeout: "fail",
  cancelled: "secondary",
};

export function progressLabel(progress: Progress): string {
  const { total, ran, evaluated } = progress;
  const label = `${ran}/${total} ran · ${evaluated}/${total} evaluated`;
  return progress.errored ? `${label} · ${progress.errored} errored` : label;
}

export function shortProgress(progress: Progress): string {
  const short = `${progress.evaluated}/${progress.total}`;
  return progress.errored ? `${short} · ${progress.errored} err` : short;
}

export function erroredNote(errored: number, total: number): string | null {
  if (!errored) return null;
  return `${errored} of ${total} cases errored: no answer, so they are left out of every score.`;
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
