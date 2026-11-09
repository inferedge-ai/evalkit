import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { queryClient } from "./lib/query";
import type { Me, WorkspaceInfo } from "./lib/types";
import { MeContext } from "./shell/capabilities";
import { WorkspaceGate } from "./shell/workspace";

export const devWorkspace: WorkspaceInfo = {
  id: 2,
  name: "dev",
  type: "shared",
  archived: false,
  promotes_to: null,
  promoted_from: null,
};

export const failure = (detail: string) =>
  Response.json({ detail }, { status: 500 });

export function member(roles: Me["roles"]): Me {
  return {
    username: "akshat",
    is_admin: false,
    email: null,
    kind: "human",
    personal_workspace: "akshat",
    roles,
  };
}

type Route = (
  url: string,
  init?: RequestInit,
) => Promise<Response | undefined> | Response | undefined;

export function stubServer(route: Route) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const answer = await route(url, init);
      if (answer) return answer;
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([devWorkspace]);
      return Response.json([]);
    }),
  );
}

export function renderPage(me: Me, path: string, routes: ReactNode) {
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route element={<WorkspaceGate />}>{routes}</Route>
          </Routes>
        </MemoryRouter>
      </MeContext.Provider>
    </QueryClientProvider>,
  );
}

export function closePage() {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
}
