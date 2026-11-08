import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { getApiData, useApi } from "../lib/query";
import type { WorkspaceInfo } from "../lib/types";
import { orderWorkspaces, sectionRoot } from "../lib/workspace";
import { useMe } from "./capabilities";

const KEY = "evalkit.workspace";
const WORKSPACES = "/workspaces?include_archived=true";

interface WorkspaceCtx {
  workspace: WorkspaceInfo;
  workspaces: WorkspaceInfo[];
  select: (name: string) => void;
  // Set when select() moved to a different workspace; the switcher flashes
  // for SWITCH_FLASH_MS after it so a followed link makes the change visible.
  switchedAt: number | null;
}

export const SWITCH_FLASH_MS = 1500;

const Ctx = createContext<WorkspaceCtx | null>(null);

export function useWorkspace(): WorkspaceCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useWorkspace outside WorkspaceGate");
  return ctx;
}

export function useFollowWorkspace(name: string | undefined) {
  const { select } = useWorkspace();
  useEffect(() => {
    if (name) select(name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);
}

export function useSwitchWorkspace(): (name: string) => void {
  const { select } = useWorkspace();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  return (name: string) => {
    select(name);
    const root = sectionRoot(pathname);
    if (root && (root !== pathname || search)) navigate(root);
  };
}

export function WorkspaceGate() {
  const me = useMe();
  const listed = useApi<WorkspaceInfo[]>(WORKSPACES, {
    select: (all) => orderWorkspaces(all, me.personal_workspace),
  });
  const workspaces = listed.data ?? null;
  const [current, setCurrent] = useState<string | null>(() =>
    localStorage.getItem(KEY),
  );

  const [switchedAt, setSwitchedAt] = useState<number | null>(null);
  const select = useCallback((name: string) => {
    // The cache, not this render's list: a caller may select a workspace
    // right after the refetch that added it.
    const known = getApiData<WorkspaceInfo[]>(WORKSPACES);
    if (!known?.some((w) => w.name === name)) return;
    localStorage.setItem(KEY, name);
    setCurrent((previous) => {
      if (previous !== null && previous !== name) setSwitchedAt(Date.now());
      return name;
    });
  }, []);

  if (listed.error && !workspaces)
    return <div className="pane-status error">{String(listed.error)}</div>;
  if (!workspaces) return <div className="pane-status">Loading…</div>;
  const workspace =
    workspaces.find((w) => w.name === current) ??
    workspaces.find((w) => w.name === me.personal_workspace) ??
    workspaces[0];
  if (!workspace)
    return (
      <div className="pane-status">
        No workspaces yet. An admin creates one with{" "}
        <span className="font-mono">
          evalkit workspaces create &lt;name&gt;
        </span>
        .
      </div>
    );
  return (
    <Ctx.Provider value={{ workspace, workspaces, select, switchedAt }}>
      <Outlet />
    </Ctx.Provider>
  );
}
