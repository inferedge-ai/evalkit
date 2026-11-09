// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import type { CaseView, Me, WorkspaceInfo } from "../lib/types";
import { MeContext } from "../shell/capabilities";
import { WorkspaceGate } from "../shell/workspace";
import { CasePage } from "./CasePage";

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

const revenue: CaseView = {
  case_ref: 1,
  case_id: "acme-revenue",
  content_hash: "abc",
  input: { question: "What was Acme's revenue in FY2025?" },
  expected: null,
  evaluators: { output: { answer_rubrics: { rubrics: [] } } },
  effective_evaluators: {},
  tags: ["income"],
  split_tags: ["income"],
  source: null,
  files: [],
  review_state: "review_requested",
  review: {
    state: "review_requested",
    requested_to: "akshat",
    updated_at: "2026-11-09T10:00:00Z",
  },
};

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

function stubServer(caseView: Promise<CaseView>) {
  const reviews: unknown[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.endsWith("/api/auth/providers"))
        return Response.json({ password: "all", oidc: null });
      if (url.endsWith("/api/workspaces?include_archived=true"))
        return Response.json([workspace]);
      if (url.endsWith("/cases/acme-revenue/review")) {
        reviews.push(JSON.parse(String(init?.body)));
        return Response.json({});
      }
      if (url.endsWith("/api/datasets/acme%40dev/cases/acme-revenue"))
        return Response.json(await caseView);
      if (url.endsWith("/events")) return Response.json({ events: [] });
      if (url.endsWith("/comments")) return Response.json({ threads: [] });
      if (url.includes("/scores")) return Response.json({ points: [] });
      return Response.json([]);
    }),
  );
  return reviews;
}

function openCase() {
  render(
    <QueryClientProvider client={queryClient}>
      <MeContext.Provider value={me}>
        <MemoryRouter
          initialEntries={["/datasets/acme@dev/cases/acme-revenue"]}
        >
          <Routes>
            <Route element={<WorkspaceGate />}>
              <Route
                path="/datasets/:ref/cases/:caseId"
                element={<CasePage />}
              />
            </Route>
          </Routes>
        </MemoryRouter>
      </MeContext.Provider>
    </QueryClientProvider>,
  );
}

test("while the case loads, its page already shows where it is", async () => {
  stubServer(new Promise(() => {}));
  openCase();
  expect(
    await screen.findByRole("heading", { name: "acme-revenue" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "acme@dev" })).toHaveAttribute(
    "href",
    "/datasets/acme%40dev/cases",
  );
});

test("a reviewer approves a case with a note", async () => {
  const reviews = stubServer(Promise.resolve(revenue));
  openCase();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Approve" }));
  await user.type(
    screen.getByPlaceholderText("Comment (optional)"),
    "Matches the 10-K.",
  );
  await user.click(screen.getByRole("button", { name: "Approve" }));
  await vi.waitFor(() =>
    expect(reviews).toEqual([
      { action: "approve", comment: "Matches the 10-K." },
    ]),
  );
});

test("a reviewer can only request changes with a comment", async () => {
  const reviews = stubServer(Promise.resolve(revenue));
  openCase();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Request changes" }),
  );
  const send = screen.getByRole("button", { name: "Request changes" });
  expect(send).toBeDisabled();
  await user.type(
    screen.getByPlaceholderText("What needs to change?"),
    "Name the fiscal year.",
  );
  await user.click(send);
  await vi.waitFor(() =>
    expect(reviews).toEqual([
      { action: "request_changes", comment: "Name the fiscal year." },
    ]),
  );
});

test("the case's full evaluator config is offered once", async () => {
  stubServer(Promise.resolve(revenue));
  openCase();
  await screen.findByRole("button", { name: "Approve" });
  expect(screen.getAllByText("Full evaluator config")).toHaveLength(1);
});
