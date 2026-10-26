import type { Me, MemberRow, WorkspaceInfo, WorkspaceRole } from "./types";

// Mirrors core/access.py: everyone reads; a personal workspace is written
// by its owner only, a shared one by admins and its writers.
function holds(me: Me, workspace: string, role: WorkspaceRole): boolean {
  return (me.roles[workspace] ?? []).includes(role);
}

export function canWrite(
  me: Me,
  ws: Pick<WorkspaceInfo, "name" | "type">,
): boolean {
  if (ws.type === "personal") return ws.name === me.personal_workspace;
  return me.is_admin || holds(me, ws.name, "writer");
}

export function isReviewerOf(me: Me, workspace: string): boolean {
  return holds(me, workspace, "reviewer");
}

export function canManageMembers(
  me: Me,
  ws: Pick<WorkspaceInfo, "type">,
): boolean {
  return me.is_admin && ws.type === "shared";
}

export function canUseAgent(
  me: Me,
  agent: { workspace: string; workspace_type: "personal" | "shared" },
): boolean {
  return (
    agent.workspace_type === "shared" ||
    agent.workspace === me.personal_workspace
  );
}

const ROLE_ORDER: WorkspaceRole[] = ["writer", "reviewer"];

export interface Member {
  username: string;
  grants: MemberRow[];
}

export function groupMembers(rows: MemberRow[]): Member[] {
  const byUser = new Map<string, MemberRow[]>();
  for (const row of rows)
    byUser.set(row.username, [...(byUser.get(row.username) ?? []), row]);
  return [...byUser]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([username, grants]) => ({
      username,
      grants: grants.sort(
        (a, b) => ROLE_ORDER.indexOf(a.role) - ROLE_ORDER.indexOf(b.role),
      ),
    }));
}
