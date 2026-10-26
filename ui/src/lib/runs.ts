import type { RunListItem } from "./types";

// The display title for a run, matched by id in a listing; null when the run
// isn't present or has no title.
export function runTitle(runs: RunListItem[], runId: string): string | null {
  return runs.find((r) => r.run_id === runId)?.title ?? null;
}

export function experimentCaseLink(
  runs: RunListItem[],
  runId: string,
): string | null {
  const run = runs.find((r) => r.run_id === runId);
  if (!run?.experiment_id || !run.case_id) return null;
  return `/experiments/${run.experiment_id}/${encodeURIComponent(run.case_id)}`;
}

export function trajectoryAvailable(
  status: string | null | undefined,
): boolean {
  return status === "valid" || status === "partial";
}

// The chip next to a run's trajectory: where it came from, which mapping
// converted it, and whether the session was still growing when it was stored.
export function trajectoryBadge(
  meta: Record<string, unknown>,
  status: string | null | undefined,
  mappingVersion: number | null | undefined,
): string | null {
  const parts: string[] = [];
  if (meta.trajectory_source === "otel") {
    parts.push("traces");
    parts.push(
      mappingVersion != null ? `mapping v${mappingVersion}` : "no mapping",
    );
  }
  if (status === "partial") parts.push("partial");
  return parts.length ? parts.join(" · ") : null;
}
