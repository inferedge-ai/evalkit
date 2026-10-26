import { useEffect, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { logout } from "../lib/api";
import { Select } from "../lib/Select";
import { othersPersonal, workspaceHint } from "../lib/workspace";
import type { WorkspaceInfo } from "../lib/types";
import { useMe } from "./capabilities";
import { JumpPalette } from "./JumpPalette";
import { ADMIN_NAV, MORE_NAV, NAV } from "./nav";
import { useTheme } from "./theme";
import { SWITCH_FLASH_MS, useSwitchWorkspace, useWorkspace } from "./workspace";

function InferEdgeLockup() {
  return (
    <span
      aria-label="InferEdge"
      className="num inline-flex items-baseline font-mono text-meta tracking-[0.08em] whitespace-nowrap uppercase select-none"
    >
      <span className="font-normal text-n-400">Infer</span>
      <span className="font-normal text-brand" aria-hidden>
        /
      </span>
      <span className="font-bold text-n-700">Edge</span>
    </span>
  );
}

function WorkspaceSwitcher() {
  const me = useMe();
  const { workspace, workspaces, switchedAt } = useWorkspace();
  const switchTo = useSwitchWorkspace();
  const [flash, setFlash] = useState(false);
  useEffect(() => {
    if (switchedAt === null) return;
    setFlash(true);
    const timer = setTimeout(() => setFlash(false), SWITCH_FLASH_MS);
    return () => clearTimeout(timer);
  }, [switchedAt]);
  const option = (ws: WorkspaceInfo) => ({
    value: ws.name,
    label: `@${ws.name}`,
    hint: workspaceHint(ws, me.personal_workspace),
  });
  const others = workspaces.filter((ws) =>
    othersPersonal(ws, me.personal_workspace),
  );
  return (
    <div
      className={`flex items-center gap-1.5 rounded-md ${flash ? "ws-switched" : ""}`}
      aria-live="polite"
    >
      <span className="text-micro tracking-wide text-ink-4 uppercase">
        {flash ? `Switched to @${workspace.name}` : "Workspace"}
      </span>
      <Select
        ariaLabel="workspace"
        title="workspace (⌘K, w)"
        value={workspace.name}
        onChange={switchTo}
        options={workspaces
          .filter((ws) => !othersPersonal(ws, me.personal_workspace))
          .map(option)}
        more={{
          label: `Show ${others.length} other personal workspace${others.length === 1 ? "" : "s"}`,
          options: others.map(option),
        }}
      />
      {othersPersonal(workspace, me.personal_workspace) && (
        <span className="text-micro text-ink-4">read-only</span>
      )}
    </div>
  );
}

const navClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-md px-2.5 py-1 text-lede transition-colors ${
    isActive ? "bg-info-bg font-medium text-brand" : "text-ink-2 hover:bg-fill"
  }`;

function MoreMenu() {
  const me = useMe();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const groups = me.is_admin ? [MORE_NAV, ADMIN_NAV] : [MORE_NAV];
  const active = groups
    .flat()
    .find((n) => pathname === n.to || pathname.startsWith(`${n.to}/`));

  useEffect(() => setOpen(false), [pathname]);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        className={navClass({ isActive: !!active })}
        onClick={() => setOpen((o) => !o)}
      >
        {active ? active.label : "More"} <span className="text-ink-4">▾</span>
      </button>
      {open && (
        <div role="menu" className="select-menu">
          <div className="select-list">
            {groups.map((group, i) => (
              <div key={i} className={i ? "select-more mt-1 pt-1" : ""}>
                {group.map((n) => (
                  <NavLink
                    key={n.to}
                    to={n.to}
                    role="menuitem"
                    onClick={() => setOpen(false)}
                    className={({ isActive }) =>
                      `select-option ${isActive ? "font-medium text-brand" : ""}`
                    }
                  >
                    {n.label}
                  </NavLink>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function Shell() {
  const me = useMe();
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center gap-4 border-b border-line bg-surface px-4 py-2">
        <NavLink to="/" className="flex items-baseline gap-2">
          <InferEdgeLockup />
          <span className="font-mono text-head font-semibold tracking-tight text-brand">
            Evalkit
          </span>
        </NavLink>
        <WorkspaceSwitcher />
        <nav className="ml-2 flex items-center gap-0.5">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} className={navClass}>
              {n.label}
            </NavLink>
          ))}
          <MoreMenu />
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <span className="chip muted" title="signed in as">
            {me.username}
          </span>
          <button
            className="btn"
            title="search datasets, experiments, cases, runs and pages"
            onClick={() => window.dispatchEvent(new Event("jump-palette:open"))}
          >
            Search <span className="num font-mono text-ink-4">⌘K</span>
          </button>
          <button
            className="btn"
            title={`switch to ${theme === "dark" ? "light" : "dark"} mode`}
            onClick={toggle}
          >
            {theme === "dark" ? "☀ Light" : "☾ Dark"}
          </button>
          <button
            className="btn btn-quiet"
            onClick={() => logout().finally(() => navigate("/login"))}
          >
            Sign out
          </button>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">
        <Outlet />
      </main>
      <JumpPalette />
    </div>
  );
}
