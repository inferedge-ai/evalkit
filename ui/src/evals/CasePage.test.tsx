// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { CaseView } from "../lib/types";
import {
  closePage,
  failure,
  member,
  renderPage,
  stubServer,
} from "../testFixtures";
import { CasePage } from "./CasePage";

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

afterEach(closePage);

const CASE_URL = "/api/datasets/acme%40dev/cases/acme-revenue";

function serveCase(
  route: (url: string) => Promise<Response> | Response | undefined = () =>
    undefined,
) {
  const reviews: unknown[] = [];
  stubServer(async (url, init) => {
    const answer = await route(url);
    if (answer) return answer;
    if (url.endsWith("/cases/acme-revenue/review")) {
      reviews.push(JSON.parse(String(init?.body)));
      return Response.json({});
    }
    if (url.endsWith(CASE_URL)) return Response.json(revenue);
    if (url.endsWith("/events")) return Response.json({ events: [] });
    if (url.endsWith("/comments")) return Response.json({ threads: [] });
    if (url.includes("/scores")) return Response.json({ points: [] });
  });
  return reviews;
}

function openCase() {
  renderPage(
    member({ dev: ["reviewer"] }),
    "/datasets/acme@dev/cases/acme-revenue",
    <Route path="/datasets/:ref/cases/:caseId" element={<CasePage />} />,
  );
}

test("while the case loads, its page already shows where it is", async () => {
  serveCase((url) =>
    url.endsWith(CASE_URL) ? new Promise(() => {}) : undefined,
  );
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
  const reviews = serveCase();
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
  const reviews = serveCase();
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
  serveCase();
  openCase();
  await screen.findByRole("button", { name: "Approve" });
  expect(screen.getAllByText("Full evaluator config")).toHaveLength(1);
});

test("a case whose YAML sets no evaluators says so, and that overlays can add some", async () => {
  serveCase((url) =>
    url.endsWith(CASE_URL)
      ? Response.json({ ...revenue, evaluators: {} })
      : undefined,
  );
  openCase();
  expect(
    await screen.findByText("No evaluators in this case's YAML"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Overlays can add evaluators for the agents they name."),
  ).toBeInTheDocument();
});

test("a case that fails to load shows the error under its header", async () => {
  serveCase((url) =>
    url.endsWith(CASE_URL) ? failure("store unavailable") : undefined,
  );
  openCase();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "acme-revenue" }),
  ).toBeInTheDocument();
});

test("a refresh that fails keeps the case on screen and says why", async () => {
  let loads = 0;
  serveCase((url) =>
    url.endsWith(CASE_URL) && ++loads > 1
      ? failure("store unavailable")
      : undefined,
  );
  openCase();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "Approve" }));
  await user.click(screen.getByRole("button", { name: "Approve" }));
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(
    screen.getByText("What was Acme's revenue in FY2025?"),
  ).toBeInTheDocument();
});

test("when its changelog and comments cannot load, the case page says so", async () => {
  serveCase((url) =>
    url.endsWith("/events")
      ? failure("events unavailable")
      : url.endsWith("/comments")
        ? failure("comments unavailable")
        : undefined,
  );
  openCase();
  expect(await screen.findByText(/events unavailable/)).toBeInTheDocument();
  expect(await screen.findByText(/comments unavailable/)).toBeInTheDocument();
});
