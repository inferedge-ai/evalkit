import type { BadgeVariant } from "@/components/ui/badge";
import type {
  AgentInfo,
  Experiment,
  ExperimentState,
  Progress,
  Report,
} from "./types";

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
  return [
    `${ran}/${total} ran`,
    `${evaluated}/${total} evaluated`,
    ...erroredParts(progress, "errored", "partly errored"),
  ].join(" · ");
}

function erroredParts(
  progress: Progress,
  errored: string,
  partly: string,
): string[] {
  return [
    ...(progress.errored ? [`${progress.errored} ${errored}`] : []),
    ...(progress.partly_errored
      ? [`${progress.partly_errored} ${partly}`]
      : []),
  ];
}

export function shortProgress(progress: Progress): string {
  return [
    `${progress.evaluated}/${progress.total}`,
    ...erroredParts(progress, "err", "part err"),
  ].join(" · ");
}

export function selectedCases(report: Report): number {
  return Math.max(report.selected_cases, report.cases.length);
}

export function erroredNote(report: Report): string | null {
  const total = selectedCases(report);
  const notes = [];
  if (report.errored_cases)
    notes.push(
      `${report.errored_cases} of ${total} cases errored: no run completed with an answer, so they are left out of every score.`,
    );
  if (report.partly_errored_cases)
    notes.push(
      `${report.partly_errored_cases} of ${total} cases partly errored: scored only on the runs that completed.`,
    );
  return notes.length ? notes.join(" ") : null;
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
