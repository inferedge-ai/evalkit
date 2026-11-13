import type { ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Moon, Sun } from "lucide-react";
import { Button, buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { SignInLink } from "../auth/SignInLink";
import { extensions } from "../extensions";
import { BrandLink } from "./Shell";
import { useTheme } from "./theme";

export function PublicShell({ children }: { children?: ReactNode }) {
  const location = useLocation();
  const { theme, toggle } = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex min-h-12 shrink-0 items-center gap-4 border-b bg-card px-4">
        <BrandLink to={extensions.landingPage ? "/" : "/docs"} />
        <NavLink
          to="/docs"
          className={({ isActive }) =>
            cn(
              buttonVariants({ variant: "ghost", size: "sm" }),
              isActive && "bg-muted",
            )
          }
        >
          Docs
        </NavLink>
        <div className="ml-auto flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Switch to ${next} mode`}
            title={`Switch to ${next} mode`}
            onClick={toggle}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
          <SignInLink
            next={location.pathname + location.search}
            className={buttonVariants({ size: "sm" })}
          >
            Sign in
          </SignInLink>
        </div>
      </header>
      <main className="min-h-0 flex-1 overflow-auto">
        {children ?? <Outlet />}
      </main>
    </div>
  );
}
