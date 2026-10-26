import type { GitRevision, WorkspaceInfo } from "./types";

export function splitRef(ref: string): { name: string; workspace: string } {
  const at = ref.indexOf("@");
  if (at < 0) return { name: ref, workspace: "" };
  return { name: ref.slice(0, at), workspace: ref.slice(at + 1) };
}

export function orderWorkspaces(
  workspaces: WorkspaceInfo[],
  me: string | null,
): WorkspaceInfo[] {
  const rank = (w: WorkspaceInfo) =>
    w.name === me ? 0 : w.type === "shared" ? 1 : 2;
  return [...workspaces].sort(
    (a, b) => rank(a) - rank(b) || a.name.localeCompare(b.name),
  );
}

export function workspaceHint(ws: WorkspaceInfo, me: string | null): string {
  const kind =
    ws.name === me
      ? "yours"
      : ws.type === "shared"
        ? "shared"
        : "personal · read-only";
  return ws.archived ? `${kind} · archived` : kind;
}

export function othersPersonal(ws: WorkspaceInfo, me: string | null): boolean {
  return ws.type === "personal" && ws.name !== me;
}

const SCOPED_SECTIONS = [
  "datasets",
  "experiments",
  "reviews",
  "runs",
  "agents",
  "workspace",
];

export function sectionRoot(pathname: string): string | null {
  const section = pathname.split("/")[1] ?? "";
  if (!SCOPED_SECTIONS.includes(section)) return null;
  return section === "workspace" ? pathname : `/${section}`;
}

export function gitRevision(value: unknown): GitRevision | null {
  if (!value || typeof value !== "object") return null;
  const rev = value as Record<string, unknown>;
  const out: GitRevision = {};
  if (typeof rev.sha === "string") out.sha = rev.sha;
  if (typeof rev.branch === "string") out.branch = rev.branch;
  if (typeof rev.dirty === "boolean") out.dirty = rev.dirty;
  return out.sha || out.branch ? out : null;
}
