// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import type { Me, WorkspaceInfo } from "../lib/types";
import { MeContext } from "../shell/capabilities";
import { WorkspaceGate } from "../shell/workspace";
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
};

// jsdom has no layout; the select scrolls its highlighted option into view.
Element.prototype.scrollIntoView = () => {};

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

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
