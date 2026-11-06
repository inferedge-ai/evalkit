import { Outlet } from "react-router-dom";
import { useOptionalMe } from "./capabilities";
import { PublicShell } from "./PublicShell";
import { Shell } from "./Shell";
import { WorkspaceGate } from "./workspace";

export function WorkspaceOrPublic() {
  return useOptionalMe() ? <WorkspaceGate /> : <PublicShell />;
}

export function ShellOrBare() {
  return useOptionalMe() ? <Shell /> : <Outlet />;
}
