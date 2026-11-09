// @vitest-environment jsdom
import { act, screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { refreshQueries } from "../lib/query";
import type { CaseView, RunDetail } from "../lib/types";
import {
  closePage,
  experiment,
  failure,
  member,
  renderPage,
  stubServer,
} from "../testFixtures";
import { RunPage } from "./RunPage";

const refundPolicy: CaseView = {
  case_ref: 1,
  case_id: "refund-policy",
  content_hash: "abc",
  input: { question: "How long do refunds take?" },
  expected: null,
  evaluators: {},
  effective_evaluators: {},
  tags: [],
  split_tags: [],
  source: null,
  files: [],
  review_state: null,
};

const run: RunDetail = {
  experiment_run_id: 40,
  case_id: "refund-policy",
  repetition: 1,
  agent_run_id: 90,
  agent_run: {
    harness_run_id: "run-90",
    status: "completed",
    answer: "Refunds take five working days.",
    output: null,
    error: null,
    started_at: "2026-11-01T10:00:00Z",
    ended_at: "2026-11-01T10:00:20Z",
    trajectory_key: null,
    trajectory_status: null,
    metrics: {},
    title: null,
  },
  evaluations: [],
};

afterEach(closePage);

const RUNS_URL = "/api/experiments/12/runs";

function serveRun(
  route: (url: string) => Promise<Response> | Response | undefined = () =>
    undefined,
) {
  stubServer(async (url) => {
    const answer = await route(url);
    if (answer) return answer;
    if (url.endsWith("/api/experiments/12")) return Response.json(experiment);
    if (url.endsWith(RUNS_URL)) return Response.json([run]);
    if (url.endsWith("/api/experiments/12/cases"))
      return Response.json([refundPolicy]);
  });
}

function openRun(tab = "") {
  renderPage(
    member({ dev: ["writer"] }),
    `/experiments/12/refund-policy${tab}`,
    <Route
      path="/experiments/:experimentId/:exampleId"
      element={<RunPage />}
    />,
  );
}

test("while the run loads, its page already shows where it is", async () => {
  serveRun((url) =>
    url.endsWith(RUNS_URL) ? new Promise(() => {}) : undefined,
  );
  openRun();
  expect(
    await screen.findByRole("heading", { name: "refund-policy" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Experiments" })).toHaveAttribute(
    "href",
    "/experiments",
  );
});

test("a run that fails to load shows the error under its header", async () => {
  serveRun((url) =>
    url.endsWith(RUNS_URL) ? failure("store unavailable") : undefined,
  );
  openRun();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "refund-policy" }),
  ).toBeInTheDocument();
});

test("a refresh that fails keeps the run on screen and says why", async () => {
  let loads = 0;
  serveRun((url) =>
    url.endsWith(RUNS_URL) && ++loads > 1
      ? failure("store unavailable")
      : undefined,
  );
  openRun();
  expect(
    await screen.findByText("Refunds take five working days."),
  ).toBeInTheDocument();
  await act(refreshQueries);
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(
    screen.getByText("Refunds take five working days."),
  ).toBeInTheDocument();
});

test("a case that has not run yet says so under its header, with a way back to the experiment", async () => {
  serveRun((url) => (url.endsWith(RUNS_URL) ? Response.json([]) : undefined));
  openRun();
  expect(
    await screen.findByText("This case has not run yet"),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("heading", { name: "refund-policy" }),
  ).toBeInTheDocument();
  expect(
    screen.getByRole("link", { name: "Back to the experiment" }),
  ).toHaveAttribute("href", "/experiments/12");
});

test("a case without runs is not reported missing while the experiment's cases load", async () => {
  serveRun((url) =>
    url.endsWith(RUNS_URL)
      ? Response.json([])
      : url.endsWith("/api/experiments/12/cases")
        ? new Promise(() => {})
        : undefined,
  );
  openRun();
  expect(
    await screen.findByRole("heading", { name: "refund-policy" }),
  ).toBeInTheDocument();
  await expect(
    screen.findByRole("heading", { name: "Not found" }, { timeout: 300 }),
  ).rejects.toThrow();
});

test("a run with no evaluators and no scores says so on its Scores tab", async () => {
  serveRun();
  openRun("?tab=scores");
  expect(await screen.findByText("No scores for this run")).toBeInTheDocument();
});
