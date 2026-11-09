// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import type { Me, QueueItem, WorkspaceInfo } from "../lib/types";
import { MeContext } from "../shell/capabilities";
import { WorkspaceGate } from "../shell/workspace";
import { ReviewsPage } from "./ReviewsPage";

const me: Me = {
  username: "akshat",
  is_admin: false,
  email: null,
  kind: "human",
  personal_workspace: "akshat",
  roles: { dev: ["reviewer"] },
};

const workspace: WorkspaceInfo = {
  id: 2,
  name: "dev",
  type: "shared",
  archived: false,
  promotes_to: null,
  promoted_from: null,
};

function queued(caseId: string): QueueItem {
  return {
    dataset: "acme@dev",
    case_id: caseId,
    requested_to: "akshat",
    requested_by: "admin",
    author: "admin",
    requested_at: "2026-11-09T10:00:00Z",
    input: `What about ${caseId}?`,
  };
}

afterEach(() => {
  cleanup();
  queryClient.clear();
  vi.unstubAllGlobals();
});

function openReviews() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      if (
        url.includes("/api/review/queue?") &&
        url.includes("state=changes_requested")
      )
        return Response.json({ items: [queued("acme-gross-margin")] });
      if (url.includes("/api/review/queue?") && url.includes("reviewer=akshat"))
        return Response.json({ items: [queued("acme-revenue")] });
      return Response.json({ items: [] });
    }),
  );
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <MemoryRouter initialEntries={["/reviews"]}>
          <Routes>
            <Route element={<WorkspaceGate />}>
              <Route path="/reviews" element={<ReviewsPage />} />
              <Route
                path="/datasets/:ref/cases/:caseId"
                element={<BackButton />}
              />
            </Route>
          </Routes>
        </MemoryRouter>
      </MeContext.Provider>
    </QueryClientProvider>,
  );
}

function BackButton() {
  const navigate = useNavigate();
  return <button onClick={() => navigate(-1)}>Back</button>;
}

test("a reviewer switches from their queue to the cases sent back to authors", async () => {
  openReviews();
  expect(
    await screen.findByRole("link", { name: "acme-revenue" }),
  ).toBeInTheDocument();
  await userEvent
    .setup()
    .click(screen.getByRole("tab", { name: "Changes requested" }));
  expect(
    await screen.findByRole("link", { name: "acme-gross-margin" }),
  ).toBeInTheDocument();
  expect(screen.getByText("1 awaiting an edit")).toBeInTheDocument();
});

test("Back from a case returns to the queue the reviewer was on", async () => {
  openReviews();
  const user = userEvent.setup();
  await screen.findByRole("link", { name: "acme-revenue" });
  await user.click(screen.getByRole("tab", { name: "Changes requested" }));
  await user.click(
    await screen.findByRole("link", { name: "acme-gross-margin" }),
  );
  await user.click(await screen.findByRole("button", { name: "Back" }));
  expect(
    await screen.findByRole("link", { name: "acme-gross-margin" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("tab", { name: "Changes requested" }),
  ).toHaveAttribute("aria-selected", "true");
});
