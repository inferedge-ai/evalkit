// @vitest-environment jsdom
import { screen, within } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { AgentInfo, WorkspaceRole } from "../lib/types";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { AgentsPage } from "./AgentsPage";

// The agent form imports a file outside ui/ that jsdom runs can't load.
vi.mock("./AgentForm", () => ({ AgentForm: () => null }));

function agent(name: string): AgentInfo {
  return {
    id: 7,
    name,
    workspace: "dev",
    ref: `${name}@dev`,
    slug: `${name}@dev`,
    workspace_type: "shared",
    harness_url: null,
    trajectory_source: "atif",
    repo_url: null,
    settings: {
      max_concurrent_runs: 4,
      trajectory_wait_seconds: 30,
      run_timeout_seconds: 600,
      poll_interval_seconds: 2,
      stop_grace_seconds: 10,
      provides: null,
    },
    driver: "sdk",
    archived: false,
    has_api_key: false,
  };
}

afterEach(closePage);

function openAgents(agents: AgentInfo[], roles: WorkspaceRole[] = ["writer"]) {
  stubServer((url) => {
    if (url.includes("/api/agents?")) return Response.json(agents);
  });
  renderPage(
    member({ dev: roles }),
    "/agents",
    <Route path="/agents" element={<AgentsPage />} />,
  );
}

test("a workspace without agents says so and how to add one", async () => {
  openAgents([]);
  expect(await screen.findByText("No agents in @dev")).toBeInTheDocument();
  expect(screen.getByText(/evalkit agents create/)).toBeInTheDocument();
});

test("a reader of a workspace without agents is told it is read-only for them", async () => {
  openAgents([], ["reviewer"]);
  expect(await screen.findByText("No agents in @dev")).toBeInTheDocument();
  expect(
    screen.getByText("This workspace is read-only for you."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/evalkit agents create/)).toBeNull();
});

test("the page header counts the workspace's agents", async () => {
  openAgents([agent("billing"), agent("support")]);
  const header = await screen.findByRole("banner");
  expect(await within(header).findByText("2 agents")).toBeInTheDocument();
});
