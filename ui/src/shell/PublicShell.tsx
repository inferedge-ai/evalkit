import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { BrandLink, navClass } from "./Shell";
import { useTheme } from "./theme";

export function PublicShell({ children }: { children?: ReactNode }) {
  const { pathname } = useLocation();
  const { theme, toggle } = useTheme();
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex items-center gap-4 border-b border-line bg-surface px-4 py-2">
        <BrandLink />
        <nav className="ml-2 flex items-center gap-0.5">
          <NavLink to="/docs" className={navClass}>
            Docs
          </NavLink>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <button
            className="btn"
            title={`switch to ${theme === "dark" ? "light" : "dark"} mode`}
            onClick={toggle}
          >
            {theme === "dark" ? "☀ Light" : "☾ Dark"}
          </button>
          {!pathname.startsWith("/login") && (
            <NavLink to="/login" className="btn btn-primary">
              Sign in
            </NavLink>
          )}
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">
        {children ?? <Outlet />}
      </main>
    </div>
  );
}
