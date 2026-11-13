// @vitest-environment jsdom
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, useNavigate, type NavigateFunction } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { CaseView, ExperimentInfo, Report } from "../lib/types";
import {
  closePage,
  experiment,
  failure,
  member,
  renderPage,
  scorecard,
  stubServer,
} from "../testFixtures";
import { ExperimentPage } from "./ExperimentPage";

const failedWithPendingRuns: ExperimentInfo = {
  ...experiment,
  status: "failed",
  status_error: "harness unreachable",
  progress: { total: 4, ran: 2, evaluated: 2 },
  case_states: {},
  resumable: true,
  pending_runs: true,
};

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

const scored: Report = {
  ...scorecard,
  cases: [
    {
      case_id: "refund-policy",
      scores: { accuracy: 0.5 },
      explanations: {},
      gauges: {},
      errors: {},
      run_errors: [],
      repetitions: 1,
      overall: 0.5,
      baseline_overall: null,
      delta: null,
    },
  ],
  evaluators: [
    {
      name: "accuracy",
      score: 0.5,
      cases: 1,
      errors: 0,
      bucket: "content",
      source: "builtin",
    },
  ],
  buckets: { content: 0.5 },
  evaluator_columns: ["accuracy"],
};

afterEach(() => {
  closePage();
  localStorage.clear();
  vi.useRealTimers();
});

// Stands in for the browser's Back button, which works while a dialog is open.
let navigateTo: NavigateFunction = () => {};
function History() {
  navigateTo = useNavigate();
  return null;
}

function openExperiment(id = 12) {
  renderPage(
    member({ dev: ["writer"] }),
    `/experiments/${id}`,
    <Route
      path="/experiments/:experimentId"
      element={
        <>
          <History />
          <ExperimentPage />
        </>
      }
    />,
  );
}

function serveExperiment(status: ExperimentInfo = failedWithPendingRuns) {
  const reevaluations: string[] = [];
  stubServer((url, init) => {
    if (url.endsWith("/evaluate")) {
      reevaluations.push(init?.method ?? "GET");
      return Response.json({});
    }
    const id = url.match(/\/api\/experiments\/(\d+)/)?.[1];
    if (id && url.endsWith(`/api/experiments/${id}`))
      return Response.json({ ...status, id: Number(id) });
    if (id && url.endsWith("/scorecard"))
      return Response.json({ ...scorecard, experiment_id: id });
  });
  return reevaluations;
}

test("re-evaluating an experiment with unfinished runs asks first, and only re-evaluates once confirmed", async () => {
  const reevaluations = serveExperiment();
  const user = userEvent.setup();
  openExperiment();

  await user.click(await screen.findByRole("button", { name: "Re-evaluate" }));
  const confirm = await screen.findByRole("alertdialog", {
    name: "Re-evaluate anyway?",
  });
  expect(confirm).toHaveAccessibleDescription(
    "Some runs of this experiment have not completed. Resume first to run them; re-evaluating now scores only the runs that finished.",
  );
  await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
  );
  expect(reevaluations).toEqual([]);

  await user.click(screen.getByRole("button", { name: "Re-evaluate" }));
  await user.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Re-evaluate",
    }),
  );
  await waitFor(() => expect(reevaluations).toEqual(["POST"]));
});

test("double-clicking the confirmation re-evaluates once", async () => {
  // jsdom has no animations, so Base UI would unmount the closing dialog at
  // once; a browser keeps it on screen, and clickable, for its exit animation.
  Element.prototype.getAnimations = () => [];
  const reevaluations = serveExperiment();
  const user = userEvent.setup();
  openExperiment();

  await user.click(await screen.findByRole("button", { name: "Re-evaluate" }));
  await user.dblClick(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Re-evaluate",
    }),
  );
  await waitFor(() => expect(reevaluations).toEqual(["POST"]));
  delete (Element.prototype as { getAnimations?: unknown }).getAnimations;
});

test("a confirmation left open on one experiment is gone after moving to another", async () => {
  const reevaluations = serveExperiment();
  const user = userEvent.setup();
  openExperiment();

  await user.click(await screen.findByRole("button", { name: "Re-evaluate" }));
  await screen.findByRole("alertdialog");
  act(() => navigateTo("/experiments/11"));

  await screen.findByRole("button", { name: "Re-evaluate" });
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
  );
  expect(reevaluations).toEqual([]);
});

async function expectNoEmptyCaseTable() {
  await screen.findByRole("button", { name: "Re-evaluate" });
  await expect(
    screen.findByText("This experiment pinned no cases", {}, { timeout: 300 }),
  ).rejects.toThrow();
  expect(screen.queryByText("0 cases")).toBeNull();
}

test("while the experiment's cases load, the case table shows it is loading and is not shown empty", async () => {
  stubServer((url) =>
    url.endsWith("/api/experiments/12/cases")
      ? new Promise(() => {})
      : url.endsWith("/api/experiments/12")
        ? Response.json(experiment)
        : url.endsWith("/api/experiments/12/scorecard")
          ? Response.json(scorecard)
          : undefined,
  );
  openExperiment();
  await expectNoEmptyCaseTable();
  expect(await screen.findAllByText("Loading...")).not.toHaveLength(0);
});

test("an experiment that pinned no cases says so in place of the case table", async () => {
  serveExperiment({ ...experiment, case_states: {} });
  openExperiment();
  expect(
    await screen.findByText("This experiment pinned no cases"),
  ).toBeInTheDocument();
  expect(screen.queryByText("0 cases")).toBeNull();
});

test("when the experiment's cases cannot load, the page says so instead of an empty case table", async () => {
  stubServer((url) =>
    url.endsWith("/api/experiments/12/cases")
      ? failure("store unavailable")
      : url.endsWith("/api/experiments/12")
        ? Response.json(experiment)
        : url.endsWith("/api/experiments/12/scorecard")
          ? Response.json(scorecard)
          : undefined,
  );
  openExperiment();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  await expectNoEmptyCaseTable();
});

test("a saved sort on an evaluator column survives the case table's loading state", async () => {
  localStorage.setItem(
    "evalkit.table.experiment-cases",
    JSON.stringify([
      { colId: "case" },
      { colId: "avg", sort: "asc", sortIndex: 0 },
      { colId: "accuracy", sort: "desc", sortIndex: 1 },
    ]),
  );
  let showScores = () => {};
  const scores = new Promise<void>((resolve) => (showScores = resolve));
  stubServer(async (url) => {
    if (url.endsWith("/api/experiments/12")) return Response.json(experiment);
    if (url.endsWith("/api/experiments/12/cases"))
      return Response.json([refundPolicy]);
    if (url.endsWith("/api/experiments/12/scorecard")) {
      await scores;
      return Response.json(scored);
    }
  });
  openExperiment();
  await waitFor(() =>
    expect(
      document.querySelector('.ag-header-cell[col-id="avg"]'),
    ).not.toBeNull(),
  );
  await new Promise((settled) => setTimeout(settled, 100));
  showScores();
  await waitFor(() =>
    expect(
      document
        .querySelector('.ag-header-cell[col-id="accuracy"]')
        ?.getAttribute("aria-sort"),
    ).toBe("descending"),
  );
});

test("a running experiment whose case list fails once shows its cases on the next poll", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let casesLoads = 0;
  stubServer((url) => {
    if (url.endsWith("/api/experiments/12"))
      return Response.json({
        ...experiment,
        status: "running",
        ended_at: null,
      });
    if (url.endsWith("/api/experiments/12/scorecard"))
      return Response.json(scored);
    if (url.endsWith("/api/experiments/12/cases"))
      return ++casesLoads === 1
        ? failure("store unavailable")
        : Response.json([refundPolicy]);
  });
  openExperiment();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  await act(() => vi.advanceTimersByTimeAsync(3000));
  expect(await screen.findByText("refund-policy")).toBeInTheDocument();
  expect(screen.queryByText(/store unavailable/)).toBeNull();
});

test("a finished experiment whose case list fails does not keep asking for it", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let casesLoads = 0;
  stubServer((url) => {
    if (url.endsWith("/api/experiments/12")) return Response.json(experiment);
    if (url.endsWith("/api/experiments/12/scorecard"))
      return Response.json(scored);
    if (url.endsWith("/api/experiments/12/cases")) {
      casesLoads++;
      return failure("store unavailable");
    }
  });
  openExperiment();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  await act(() => vi.advanceTimersByTimeAsync(10_000));
  expect(casesLoads).toBe(1);
});

test("an experiment with errored runs says how many cases errored and how many each bucket covers", async () => {
  const errored: Report = {
    ...scored,
    cases: [
      ...scored.cases,
      {
        ...scored.cases[0],
        case_id: "late-fee",
        scores: {},
        run_errors: ["agent crashed"],
        overall: null,
      },
    ],
    bucket_cases: { content: 1 },
    selected_cases: 3,
    errored_cases: 1,
    partly_errored_cases: 0,
  };
  stubServer((url) =>
    url.endsWith("/api/experiments/12/cases")
      ? Response.json([refundPolicy])
      : url.endsWith("/api/experiments/12")
        ? Response.json({
            ...experiment,
            progress: { total: 2, ran: 2, evaluated: 2, errored: 1 },
          })
        : url.endsWith("/api/experiments/12/scorecard")
          ? Response.json(errored)
          : undefined,
  );
  openExperiment();
  expect(
    await screen.findByText(
      "1 of 3 cases errored: no run completed with an answer, so they are left out of every score.",
    ),
  ).toBeInTheDocument();
  expect(screen.getByText("1 of 3 cases")).toBeInTheDocument();
  expect(
    screen.getAllByText("2/2 ran · 2/2 evaluated · 1 errored").length,
  ).toBeGreaterThan(0);
});
