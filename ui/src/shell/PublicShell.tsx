import { Outlet } from "react-router-dom";

export function PublicShell() {
  return (
    <main className="h-dvh overflow-auto">
      <Outlet />
    </main>
  );
}
