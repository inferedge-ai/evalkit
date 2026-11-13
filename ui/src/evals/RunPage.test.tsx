// @vitest-environment jsdom
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

test("while the run loads, its tabs show with the open one selected", async () => {
  serveRun((url) =>
    url.endsWith(RUNS_URL) ? new Promise(() => {}) : undefined,
  );
  openRun("?tab=scores");
  expect(await screen.findByRole("tab", { name: "Scores" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByText("Question")).toBeNull();
  for (const name of ["Trajectory", "Workspace", "Cost", "Timing"])
    expect(screen.getByRole("tab", { name })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  expect(screen.getByRole("tab", { name: "Case" })).not.toHaveAttribute(
    "aria-disabled",
    "true",
  );
});

test("a case without runs whose experiment's cases fail to load shows the error, not Not found", async () => {
  serveRun((url) =>
    url.endsWith(RUNS_URL)
      ? Response.json([])
      : url.endsWith("/api/experiments/12/cases")
        ? failure("store unavailable")
        : undefined,
  );
  openRun();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Not found" })).toBeNull();
});

test("when the experiment's cases fail to load, the run page shows the error beside the run", async () => {
  serveRun((url) =>
    url.endsWith("/api/experiments/12/cases")
      ? failure("store unavailable")
      : undefined,
  );
  openRun();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(
    screen.getByText("Refunds take five working days."),
  ).toBeInTheDocument();
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

test("a case without runs keeps saying it has not run when a refresh of the experiment's cases fails", async () => {
  let caseLoads = 0;
  serveRun((url) =>
    url.endsWith(RUNS_URL)
      ? Response.json([])
      : url.endsWith("/api/experiments/12/cases") && ++caseLoads > 1
        ? failure("store unavailable")
        : undefined,
  );
  openRun();
  expect(
    await screen.findByText("This case has not run yet"),
  ).toBeInTheDocument();
  await act(refreshQueries);
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(screen.getByText("This case has not run yet")).toBeInTheDocument();
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

test("links in an answer open in a new tab, its footnotes stay on the page, and its code blocks copy", async () => {
  const answer =
    "See [the refund policy](https://acme.example/refunds).[^1]\n\n```sql\nselect 5;\n```\n\n[^1]: Annual report.\n";
  serveRun((url) =>
    url.endsWith(RUNS_URL)
      ? Response.json([{ ...run, agent_run: { ...run.agent_run, answer } }])
      : undefined,
  );
  const user = userEvent.setup();
  openRun();

  const link = await screen.findByRole("link", { name: "the refund policy" });
  expect(link).toHaveAttribute("target", "_blank");
  expect(link).toHaveAttribute("rel", "noreferrer");
  const footnote = screen.getByRole("link", { name: "1" });
  expect(footnote).not.toHaveAttribute("target");
  expect(footnote).toHaveAttribute("id");
  const code = within(screen.getByText("sql").parentElement!);
  await user.click(code.getByRole("button", { name: "copy" }));
  expect(await navigator.clipboard.readText()).toBe("select 5;");
});

test("a run with no evaluators and no scores says so on its Scores tab", async () => {
  serveRun();
  openRun("?tab=scores");
  expect(await screen.findByText("No scores for this run")).toBeInTheDocument();
});

test("a run whose trajectory is missing says why", async () => {
  const noted: RunDetail = {
    ...run,
    agent_run: {
      ...run.agent_run,
      trajectory_status: "unavailable",
      trajectory_note: "only evalkit's run span was found",
    },
  };
  serveRun((url) =>
    url.endsWith(RUNS_URL) ? Response.json([noted]) : undefined,
  );
  openRun();
  expect(
    await screen.findByText(
      "Trajectory unavailable: only evalkit's run span was found",
    ),
  ).toBeInTheDocument();
});
