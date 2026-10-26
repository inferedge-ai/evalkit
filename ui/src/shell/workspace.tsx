import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { api } from "../lib/api";
import type { WorkspaceInfo } from "../lib/types";
import { orderWorkspaces, sectionRoot } from "../lib/workspace";
import { useMe } from "./capabilities";

const KEY = "evalkit.workspace";

interface WorkspaceCtx {
  workspace: WorkspaceInfo;
  workspaces: WorkspaceInfo[];
  select: (name: string) => void;
  reload: () => Promise<WorkspaceInfo[]>;
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
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[] | null>(null);
  const [current, setCurrent] = useState<string | null>(() =>
    localStorage.getItem(KEY),
  );
  const [error, setError] = useState<string | null>(null);
  const latest = useRef<WorkspaceInfo[]>([]);

  const reload = useCallback(
    () =>
      api<WorkspaceInfo[]>("/workspaces?include_archived=true").then((all) => {
        const ordered = orderWorkspaces(all, me.personal_workspace);
        latest.current = ordered;
        setWorkspaces(ordered);
        return ordered;
      }),
    [me.personal_workspace],
  );

  useEffect(() => {
    reload().catch((e) => setError(String(e)));
  }, [reload]);

  const [switchedAt, setSwitchedAt] = useState<number | null>(null);
  const select = useCallback((name: string) => {
    if (!latest.current.some((w) => w.name === name)) return;
    localStorage.setItem(KEY, name);
    setCurrent((previous) => {
      if (previous !== null && previous !== name) setSwitchedAt(Date.now());
      return name;
    });
  }, []);

  if (error) return <div className="pane-status error">{error}</div>;
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
    <Ctx.Provider value={{ workspace, workspaces, select, reload, switchedAt }}>
      <Outlet />
    </Ctx.Provider>
  );
}
