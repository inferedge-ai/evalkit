// @vitest-environment jsdom
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, useNavigate, type NavigateFunction } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import type { ExperimentInfo } from "../lib/types";
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

afterEach(closePage);

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

test("while the scorecard loads, the case table shows the grid's own loading state", async () => {
  stubServer((url) =>
    url.endsWith("/api/experiments/12/scorecard")
      ? new Promise(() => {})
      : url.endsWith("/api/experiments/12")
        ? Response.json(experiment)
        : undefined,
  );
  openExperiment();
  expect(await screen.findByText("Cases")).toBeInTheDocument();
  expect(await screen.findByText("Loading...")).toBeInTheDocument();
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
  expect(screen.queryByText("0 cases")).toBeNull();
});
