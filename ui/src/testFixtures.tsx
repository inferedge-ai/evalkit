import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";
import { setSessionForTest } from "./auth/oidc";
import { queryClient } from "./lib/query";
import type { ExperimentInfo, Me, Report, WorkspaceInfo } from "./lib/types";
import { MeContext } from "./shell/capabilities";
import { WorkspaceGate } from "./shell/workspace";

export const devWorkspace: WorkspaceInfo = {
  id: 2,
  name: "dev",
  type: "shared",
  archived: false,
  promotes_to: null,
  promoted_from: null,
  members: 0,
};

export const experiment: ExperimentInfo = {
  id: 12,
  number: 3,
  workspace: "dev",
  dataset: "support@dev",
  agent: "bot@dev",
  agent_id: 7,
  created_by: "akshat",
  status: "completed",
  status_error: null,
  lease_holder: null,
  lease_expires_at: null,
  repetitions: 1,
  config: {
    model: null,
    judge: {
      provider: "openai",
      model: "gpt-5",
      reasoning_effort: "low",
      max_tokens: 4096,
    },
    selection: { all: true },
    revision: null,
    snapshot: {
      capabilities: {},
      trajectory_source: "atif",
      trajectory_wait_seconds: 30,
      remote_evaluators: {},
      code_evaluators: [],
      evalkit_version: null,
    },
  },
  started_at: "2026-11-01T10:00:00Z",
  ended_at: "2026-11-01T10:05:00Z",
  progress: { total: 1, ran: 1, evaluated: 1 },
  case_states: { "refund-policy": "evaluated" },
  can_change: true,
  server_may_drive: true,
  resumable: false,
  pending_runs: false,
};

export const scorecard: Report = {
  experiment_id: "12",
  name: "#3",
  dataset: "support@dev",
  metadata: {},
  cases: [],
  evaluators: [],
  buckets: {},
  bucket_cases: {},
  errored_cases: 0,
  gauges: [],
  evaluator_columns: [],
  baseline_id: null,
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

export function stubPasswordLogin(
  status: number,
  password: string,
  route: Route = () => undefined,
) {
  setSessionForTest(null);
  stubServer((url, init) => {
    if (url.endsWith("/api/auth/providers"))
      return Response.json(
        status === 200
          ? { password, oidc: { name: "Okta" } }
          : { detail: "identity provider unreachable", password },
        { status },
      );
    return route(url, init);
  });
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
