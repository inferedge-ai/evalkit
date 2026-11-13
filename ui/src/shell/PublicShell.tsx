import { Outlet } from "react-router-dom";

export function PublicShell() {
  return (
    <div className="h-dvh overflow-auto">
      <Outlet />
    </div>
  );
}
