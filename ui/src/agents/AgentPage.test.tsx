// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { AgentInfo } from "../lib/types";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { AgentPage } from "./AgentPage";

// The agent form imports a file outside ui/ that jsdom runs can't load.
vi.mock("./AgentForm", () => ({ AgentForm: () => null }));

const agent: AgentInfo = {
  id: 7,
  name: "bot",
  workspace: "dev",
  ref: "bot@dev",
  slug: "bot@dev",
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

const traced: AgentInfo = { ...agent, trajectory_source: "otel" };

const AGENT_URL = "/api/agents/bot%40dev";

afterEach(closePage);

function serveAgent(served: AgentInfo) {
  const archives: string[] = [];
  stubServer((url, init) => {
    if (url.endsWith(`${AGENT_URL}/archive`)) {
      archives.push(init?.method ?? "GET");
      return Response.json({ ...served, archived: true });
    }
    if (url.endsWith(AGENT_URL)) return Response.json(served);
    if (url.includes(`${AGENT_URL}/runs?`))
      return Response.json({ runs: [], total: 0 });
  });
  return archives;
}

function openAgent(tab: string) {
  renderPage(
    member({ dev: ["writer"] }),
    `/agents/bot@dev/${tab}`,
    <Route path="/agents/:agent/:tab" element={<AgentPage />} />,
  );
}

test("archiving an agent asks first, and only archives once confirmed", async () => {
  const archives = serveAgent(agent);
  const user = userEvent.setup();
  openAgent("overview");

  await user.click(await screen.findByRole("button", { name: "Archive" }));
  const confirm = await screen.findByRole("alertdialog", {
    name: "Archive bot@dev?",
  });
  expect(confirm).toHaveAccessibleDescription(
    "Nobody can run it until it's restored. Its runs and results stay.",
  );
  await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
  );
  expect(archives).toEqual([]);

  await user.click(screen.getByRole("button", { name: "Archive" }));
  await user.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Archive",
    }),
  );
  expect(
    await screen.findByRole("button", { name: "Restore" }),
  ).toBeInTheDocument();
  expect(screen.getByText("archived")).toBeInTheDocument();
  expect(archives).toEqual(["POST"]);
});

test("the page header says how evalkit reaches the agent and where its trajectories come from", async () => {
  serveAgent(agent);
  openAgent("overview");
  const header = await screen.findByRole("banner");
  expect(
    await within(header).findByText("SDK · ATIF from the agent"),
  ).toBeInTheDocument();
});

test("a traced agent without a mapping or sessions says how to push a mapping and that there is nothing to preview", async () => {
  serveAgent(traced);
  openAgent("mappings");
  expect(await screen.findByText("No mapping yet")).toBeInTheDocument();
  expect(screen.getByText(/evalkit traces mapping push/)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /^Rules/ })).toBeNull();
  expect(
    await screen.findByText("No sessions to preview yet"),
  ).toBeInTheDocument();
});

test("while the trace sources load, the Import tab does not say there are none", async () => {
  stubServer((url) => {
    if (url.endsWith("/api/trace-sources")) return new Promise(() => {});
    if (url.endsWith(AGENT_URL)) return Response.json(traced);
  });
  openAgent("import");
  expect(
    await screen.findByRole("heading", { name: "Range imports" }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/No trace sources yet/)).toBeNull();
});

test("importing traces without a trace source says how an admin adds one", async () => {
  serveAgent(traced);
  openAgent("import");
  expect(await screen.findByText("No trace sources yet")).toBeInTheDocument();
  expect(screen.getByText(/evalkit trace-sources create/)).toBeInTheDocument();
});
