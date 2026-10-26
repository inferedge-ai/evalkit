export type TraceImportStatus =
  "running" | "stopping" | "stopped" | "done" | "failed";

export interface TraceImport {
  id: number;
  agent: string;
  source: string;
  project: string;
  since: string;
  until: string;
  resume_from: string;
  status: TraceImportStatus;
  traces: number;
  spans: number;
  sample_session: string | null;
  error: string | null;
  created_by: string;
  created_at: string;
  finished_at: string | null;
}

export function isActiveImport(status: TraceImportStatus): boolean {
  return status === "running" || status === "stopping";
}

export function importAction(
  status: TraceImportStatus,
): "stop" | "continue" | null {
  if (status === "running") return "stop";
  if (status === "failed" || status === "stopped") return "continue";
  return null;
}

export function importFraction(
  i: Pick<TraceImport, "since" | "until" | "resume_from" | "status">,
): number {
  if (i.status === "done") return 1;
  const since = Date.parse(i.since);
  const span = Date.parse(i.until) - since;
  const reached = (Date.parse(i.resume_from) - since) / span;
  return Math.min(1, Math.max(0, reached));
}

export function localToIso(value: string): string {
  return new Date(value).toISOString();
}
