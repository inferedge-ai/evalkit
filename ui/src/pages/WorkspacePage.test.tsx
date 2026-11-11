// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import type { Me, WorkspaceInfo, WorkspaceRole } from "../lib/types";
import { MeContext } from "../shell/capabilities";
import { WorkspaceGate } from "../shell/workspace";
import {
  closePage,
  devWorkspace,
  renderPage,
  stubServer,
} from "../testFixtures";
import { WorkspacePage } from "./WorkspacePage";

// endpoint.ts reads a file outside ui/ that jsdom runs can't load.
vi.mock("../lib/endpoint", () => ({
  ENDPOINT_NOTE: "Reachable from the server.",
  PERSONAL_KEY_NOTE: "Keys live in evalkit.yaml.",
}));

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
  members: 0,
};

// jsdom has no layout; the select scrolls its highlighted option into view.
Element.prototype.scrollIntoView = () => {};

afterEach(closePage);

test("Esc closes the open kind list, then the new evaluator form, without saving", async () => {
  const writes: string[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (init?.method && init.method !== "GET") writes.push(url);
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      return Response.json([]);
    }),
  );
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <MemoryRouter initialEntries={["/workspace/evaluators"]}>
          <Routes>
            <Route element={<WorkspaceGate />}>
              <Route path="/workspace/:tab" element={<WorkspacePage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </MeContext.Provider>
    </QueryClientProvider>,
  );

  await user.click(
    await screen.findByRole("button", { name: "New evaluator" }),
  );
  await screen.findByRole("dialog", { name: "New evaluator" });
  await user.type(screen.getByLabelText("name"), "exact");
  await user.type(screen.getByLabelText("endpoint"), "https://ev.example.com");
  await user.click(screen.getByRole("button", { name: "kind" }));
  expect(screen.getByRole("listbox")).toBeInTheDocument();

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  expect(
    screen.getByRole("dialog", { name: "New evaluator" }),
  ).toBeInTheDocument();

  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  expect(writes).toEqual([]);
});

function openSettings(
  tab: "evaluators" | "members",
  roles: WorkspaceRole[] = ["writer"],
  ws: WorkspaceInfo = devWorkspace,
  isAdmin = false,
) {
  stubServer((url) => {
    if (url.endsWith("/api/workspaces?include_archived=true"))
      return Response.json([ws]);
  });
  renderPage(
    { ...me, is_admin: isAdmin, roles: { [ws.name]: roles } },
    `/settings/workspace/${tab}`,
    <Route path="/settings/workspace/:tab" element={<WorkspacePage />} />,
  );
}

test("the page header says the workspace is shared and where it promotes to", async () => {
  openSettings("evaluators", ["writer"], {
    ...devWorkspace,
    promotes_to: "prod",
  });
  const header = await screen.findByRole("banner");
  expect(
    within(header).getByText("Shared workspace · promotes to @prod"),
  ).toBeInTheDocument();
});

test("a workspace without remote evaluators says so and how to add one", async () => {
  openSettings("evaluators");
  expect(
    await screen.findByText("No remote evaluators in @dev"),
  ).toBeInTheDocument();
  expect(screen.getByText(/evalkit evaluators create/)).toBeInTheDocument();
});

test("a reader of a workspace without remote evaluators is told it is read-only for them", async () => {
  openSettings("evaluators", ["reviewer"]);
  expect(
    await screen.findByText("No remote evaluators in @dev"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("This workspace is read-only for you."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/evalkit evaluators create/)).toBeNull();
});

test("a shared workspace without members says so", async () => {
  openSettings("members");
  expect(await screen.findByText("No members in @dev")).toBeInTheDocument();
});

test("a personal workspace's Members tab says only its owner writes to it", async () => {
  openSettings("members", [], workspace);
  expect(await screen.findByText("No members in @akshat")).toBeInTheDocument();
  expect(
    screen.getByText(
      "A personal workspace has no members: only @akshat writes to it.",
    ),
  ).toBeInTheDocument();
});

test("an admin on an archived workspace is told it is archived and cannot add evaluators", async () => {
  openSettings("evaluators", [], { ...devWorkspace, archived: true }, true);
  expect(
    await screen.findByText("This workspace is archived."),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "New evaluator" })).toBeNull();
});
