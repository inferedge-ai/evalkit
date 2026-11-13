// @vitest-environment jsdom
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { refreshQueries } from "../lib/query";
import type { AgentInfo, RunList, WorkspaceRole } from "../lib/types";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { RunsBrowser } from "./RunsBrowser";

const bot: AgentInfo = {
  id: 7,
  name: "bot",
  workspace: "dev",
  ref: "bot@dev",
  slug: "bot@dev",
  workspace_type: "shared",
  harness_url: "https://bot.example.com",
  trajectory_source: "atif",
  repo_url: null,
  settings: {
    max_concurrent_runs: 4,
    trajectory_wait_seconds: 30,
    run_timeout_seconds: 600,
    poll_interval_seconds: 2,
    stop_grace_seconds: 10,
    provides: null,
    trace_source: null,
    project: null,
    import_settle_seconds: 15,
    import_wait_seconds: 120,
  },
  driver: "server",
  archived: false,
  has_api_key: true,
};

const refundRun: RunList = {
  runs: [
    {
      run_id: "run-1",
      title: "Refund question",
      user_id: "u-1",
      created_at: "2026-11-09T10:00:00Z",
    },
  ],
  total: 1,
};

const failure = (detail: string) => Response.json({ detail }, { status: 500 });

afterEach(closePage);

function openRuns(
  path = "/runs?agent=bot@dev",
  roles: WorkspaceRole[] = ["writer"],
) {
  renderPage(
    member({ dev: roles }),
    path,
    <Route path="/runs" element={<RunsBrowser />} />,
  );
}

test("a workspace without agents says so and points to the Agents page", async () => {
  stubServer((url) => {
    if (url.includes("/api/agents?")) return Response.json([]);
  });
  openRuns("/runs");
  expect(
    await screen.findByText("No active agents in @dev"),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Agents" })).toHaveAttribute(
    "href",
    "/agents",
  );
  expect(screen.queryByPlaceholderText("Search id prefix or title")).toBeNull();
});

test("a reader of a workspace without agents is not pointed at adding one", async () => {
  stubServer((url) => {
    if (url.includes("/api/agents?")) return Response.json([]);
  });
  openRuns("/runs", ["reviewer"]);
  expect(
    await screen.findByText("No active agents in @dev"),
  ).toBeInTheDocument();
  expect(screen.getByText("Runs are listed per agent.")).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Agents" })).toBeNull();
});

test("while the agents load, the runs table already shows it is loading", async () => {
  stubServer((url) =>
    url.includes("/api/agents?") ? new Promise(() => {}) : undefined,
  );
  openRuns("/runs");
  expect(await screen.findByText("Loading...")).toBeInTheDocument();
});

test("the runs table stays up while the page picks the first agent", async () => {
  let sendAgents: (() => void) | undefined;
  const runsAsked = vi.fn();
  stubServer(async (url) => {
    if (url.includes("/api/agents?")) {
      await new Promise<void>((settle) => (sendAgents = settle));
      return Response.json([bot]);
    }
    if (url.includes("/api/agents/bot%40dev/runs")) {
      runsAsked();
      return new Promise(() => {});
    }
  });
  openRuns("/runs");
  await screen.findByText("Loading...");
  await waitFor(() => expect(sendAgents).toBeDefined());
  const table = document.querySelector(".ag-root-wrapper")!;
  sendAgents!();
  await waitFor(() => expect(runsAsked).toHaveBeenCalled());
  expect(table.isConnected).toBe(true);
});

test("an agent without runs says it has none yet, and a search that matches nothing says so", async () => {
  stubServer((url) => {
    if (url.includes("/api/agents?")) return Response.json([bot]);
    if (url.includes("/api/agents/bot%40dev/runs"))
      return Response.json({ runs: [], total: 0 });
  });
  openRuns();
  expect(await screen.findByText("No runs yet.")).toBeInTheDocument();
  await userEvent.type(
    screen.getByPlaceholderText("Search id prefix or title"),
    "zz",
  );
  expect(await screen.findByText("No runs match.")).toBeInTheDocument();
});

test("when the agents cannot load, the error does not blame the harness's run listing", async () => {
  stubServer((url) => {
    if (url.includes("/api/agents?")) return failure("store unavailable");
  });
  openRuns("/runs");
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(screen.queryByText(/may not implement run listing/)).toBeNull();
});

test("when the run listing fails, the error says the harness may not implement it", async () => {
  stubServer((url) => {
    if (url.includes("/api/agents?")) return Response.json([bot]);
    if (url.includes("/api/agents/bot%40dev/runs"))
      return failure("not implemented");
  });
  openRuns();
  expect(
    await screen.findByText(
      /not implemented — the harness may not implement run listing/,
    ),
  ).toBeInTheDocument();
});

test("a refresh that fails keeps the runs on screen with the error above them", async () => {
  let down = false;
  stubServer((url) => {
    if (url.includes("/api/agents?")) return Response.json([bot]);
    if (url.includes("/api/agents/bot%40dev/runs"))
      return down ? failure("harness unreachable") : Response.json(refundRun);
  });
  openRuns();
  expect(await screen.findByText("Refund question")).toBeInTheDocument();
  down = true;
  await act(() => refreshQueries());
  expect(await screen.findByText(/harness unreachable/)).toBeInTheDocument();
  expect(screen.getByText("Refund question")).toBeInTheDocument();
});

test("each run shows its status and how long it took", async () => {
  stubServer((url) => {
    if (url.includes("/api/agents?")) return Response.json([bot]);
    if (url.includes("/api/agents/bot%40dev/runs"))
      return Response.json({
        runs: [
          { ...refundRun.runs[0], status: "completed", duration_ms: 1500 },
          {
            ...refundRun.runs[0],
            run_id: "run-2",
            title: "Card limit",
            status: "failed",
            duration_ms: 95_000,
          },
        ],
        total: 2,
      });
  });
  openRuns();
  expect(await screen.findByText("failed")).toBeInTheDocument();
  expect(screen.getByText("completed")).toBeInTheDocument();
  expect(screen.getByText("1.5s")).toBeInTheDocument();
  expect(screen.getByText("1.6m")).toBeInTheDocument();
});
