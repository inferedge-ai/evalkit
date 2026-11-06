import { Outlet } from "react-router-dom";
import { useOptionalMe } from "./capabilities";
import { PublicShell } from "./PublicShell";
import { Shell } from "./Shell";
import { WorkspaceGate } from "./workspace";

export function DocsWorkspace() {
  return useOptionalMe() ? <WorkspaceGate /> : <PublicShell />;
}

export function DocsShell() {
  return useOptionalMe() ? <Shell /> : <Outlet />;
}
