// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
  type NavigateFunction,
} from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import type { ExperimentInfo, Me, Report, WorkspaceInfo } from "../lib/types";
import { MeContext } from "../shell/capabilities";
import { WorkspaceGate } from "../shell/workspace";
import { ExperimentPage } from "./ExperimentPage";

const me: Me = {
  username: "akshat",
  is_admin: false,
  email: null,
  kind: "human",
  personal_workspace: "akshat",
  roles: { dev: ["writer"] },
};

const workspace: WorkspaceInfo = {
  id: 2,
  name: "dev",
  type: "shared",
  archived: false,
  promotes_to: null,
  promoted_from: null,
};

const failedWithPendingRuns: ExperimentInfo = {
  id: 12,
  number: 3,
  workspace: "dev",
  dataset: "support@dev",
  agent: "bot@dev",
  agent_id: 7,
  created_by: "akshat",
  status: "failed",
  status_error: "harness unreachable",
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
  progress: { total: 4, ran: 2, evaluated: 2 },
  case_states: {},
  can_change: true,
  server_may_drive: true,
  resumable: true,
  pending_runs: true,
};

const report: Report = {
  experiment_id: "12",
  name: "#3",
  dataset: "support@dev",
  metadata: {},
  cases: [],
  evaluators: [],
  buckets: {},
  gauges: [],
  evaluator_columns: [],
  baseline_id: null,
};

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

test("re-evaluating an experiment with unfinished runs asks first, and only re-evaluates once confirmed", async () => {
  const reevaluations: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      if (url.endsWith("/api/experiments/12/evaluate")) {
        reevaluations.push(init?.method ?? "GET");
        return Response.json({});
      }
      if (url.endsWith("/api/experiments/12"))
        return Response.json(failedWithPendingRuns);
      if (url.endsWith("/api/experiments/12/scorecard"))
        return Response.json(report);
      return Response.json([]);
    }),
  );
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <MemoryRouter initialEntries={["/experiments/12"]}>
          <Routes>
            <Route element={<WorkspaceGate />}>
              <Route
                path="/experiments/:experimentId"
                element={<ExperimentPage />}
              />
            </Route>
          </Routes>
        </MemoryRouter>
      </MeContext.Provider>
    </QueryClientProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "Re-evaluate" }));
  const confirm = await screen.findByRole("alertdialog", {
    name: "Re-evaluate anyway?",
  });
  expect(confirm).toHaveAccessibleDescription(
    "Some runs of this experiment have not completed. Resume first to run them; re-evaluating now scores only the runs that finished.",
  );
  await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
  );
  expect(reevaluations).toEqual([]);

  await user.click(screen.getByRole("button", { name: "Re-evaluate" }));
  await user.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Re-evaluate",
    }),
  );
  await waitFor(() => expect(reevaluations).toEqual(["POST"]));
});

test("double-clicking the confirmation re-evaluates once", async () => {
  // jsdom has no animations, so Base UI would unmount the closing dialog at
  // once; a browser keeps it on screen, and clickable, for its exit animation.
  Element.prototype.getAnimations = () => [];
  const reevaluations: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      if (url.endsWith("/api/experiments/12/evaluate")) {
        reevaluations.push(init?.method ?? "GET");
        return Response.json({});
      }
      if (url.endsWith("/api/experiments/12"))
        return Response.json(failedWithPendingRuns);
      if (url.endsWith("/api/experiments/12/scorecard"))
        return Response.json(report);
      return Response.json([]);
    }),
  );
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <MemoryRouter initialEntries={["/experiments/12"]}>
          <Routes>
            <Route element={<WorkspaceGate />}>
              <Route
                path="/experiments/:experimentId"
                element={<ExperimentPage />}
              />
            </Route>
          </Routes>
        </MemoryRouter>
      </MeContext.Provider>
    </QueryClientProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "Re-evaluate" }));
  await user.dblClick(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Re-evaluate",
    }),
  );
  await waitFor(() => expect(reevaluations).toEqual(["POST"]));
  delete (Element.prototype as { getAnimations?: unknown }).getAnimations;
});

// Stands in for the browser's Back button, which works while a dialog is open.
let navigateTo: NavigateFunction = () => {};
function History() {
  navigateTo = useNavigate();
  return null;
}

test("a confirmation left open on one experiment is gone after moving to another", async () => {
  const reevaluations: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      if (url.endsWith("/evaluate")) {
        reevaluations.push(url);
        return Response.json({});
      }
      const id = url.match(/\/api\/experiments\/(\d+)/)?.[1];
      if (id && url.endsWith(`/api/experiments/${id}`))
        return Response.json({ ...failedWithPendingRuns, id: Number(id) });
      if (id && url.endsWith("/scorecard"))
        return Response.json({ ...report, experiment_id: id });
      return Response.json([]);
    }),
  );
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <MemoryRouter initialEntries={["/experiments/12"]}>
          <History />
          <Routes>
            <Route element={<WorkspaceGate />}>
              <Route
                path="/experiments/:experimentId"
                element={<ExperimentPage />}
              />
            </Route>
          </Routes>
        </MemoryRouter>
      </MeContext.Provider>
    </QueryClientProvider>,
  );

  await user.click(await screen.findByRole("button", { name: "Re-evaluate" }));
  await screen.findByRole("alertdialog");
  act(() => navigateTo("/experiments/11"));

  await screen.findByRole("button", { name: "Re-evaluate" });
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
  );
  expect(reevaluations).toEqual([]);
});
