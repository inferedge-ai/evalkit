// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { SidebarProvider } from "@/components/ui/sidebar";
import { queryClient } from "../lib/query";
import type { AgentInfo, Me, WorkspaceInfo } from "../lib/types";
import { MeContext } from "../shell/capabilities";
import { WorkspaceGate } from "../shell/workspace";
import { AgentPage } from "./AgentPage";

// The agent form imports a file outside ui/ that jsdom runs can't load.
vi.mock("./AgentForm", () => ({ AgentForm: () => null }));

const me: Me = {
  username: "akshat",
  is_admin: false,
  email: null,
  kind: "human",
  personal_workspace: "akshat",
  roles: {},
};

const workspace: WorkspaceInfo = {
  id: 1,
  name: "akshat",
  type: "personal",
  archived: false,
  promotes_to: null,
  promoted_from: null,
};

const agent: AgentInfo = {
  id: 7,
  name: "bot",
  workspace: "akshat",
  ref: "bot@akshat",
  slug: "bot@akshat",
  workspace_type: "personal",
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

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

function stubServer() {
  const archives: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      if (url.endsWith("/api/agents/bot%40akshat/archive")) {
        archives.push(init?.method ?? "GET");
        return Response.json({ ...agent, archived: true });
      }
      if (url.endsWith("/api/agents/bot%40akshat")) return Response.json(agent);
      return Response.json({ detail: "not found" }, { status: 404 });
    }),
  );
  return archives;
}

function renderAgentPage() {
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <SidebarProvider>
          <MemoryRouter initialEntries={["/agents/bot@akshat/overview"]}>
            <Routes>
              <Route element={<WorkspaceGate />}>
                <Route path="/agents/:agent/:tab" element={<AgentPage />} />
              </Route>
            </Routes>
          </MemoryRouter>
        </SidebarProvider>
      </MeContext.Provider>
    </QueryClientProvider>,
  );
}

test("archiving an agent asks first, and only archives once confirmed", async () => {
  const archives = stubServer();
  const user = userEvent.setup();
  renderAgentPage();

  await user.click(await screen.findByRole("button", { name: "Archive" }));
  const confirm = await screen.findByRole("alertdialog", {
    name: "Archive bot@akshat?",
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
