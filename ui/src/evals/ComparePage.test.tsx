// @vitest-environment jsdom
import { act, screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { refreshQueries } from "../lib/query";
import type { CaseRow } from "../lib/types";
import {
  closePage,
  experiment,
  failure,
  member,
  renderPage,
  scorecard,
  stubServer,
} from "../testFixtures";
import { ComparePage } from "./ComparePage";

const refundPolicy: CaseRow = {
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
};

afterEach(closePage);

const COMPARE_URL = "/api/experiments/compare?base=11&compare=12";

function serveComparison(
  route: (url: string) => Promise<Response> | Response | undefined = () =>
    undefined,
) {
  stubServer(async (url) => {
    const answer = await route(url);
    if (answer) return answer;
    if (url.endsWith(COMPARE_URL))
      return Response.json({
        base: { ...scorecard, experiment_id: "11", name: "#2" },
        compare: { ...scorecard, cases: [refundPolicy] },
      });
    const id = url.match(/\/api\/experiments\/(\d+)$/)?.[1];
    if (id) return Response.json({ ...experiment, id: Number(id) });
  });
}

function openCompare(path = "/experiments/compare?base=11&compare=12") {
  renderPage(
    member({ dev: ["writer"] }),
    path,
    <Route path="/experiments/compare" element={<ComparePage />} />,
  );
}

test("with no experiments picked, the compare page says how to pick two", async () => {
  serveComparison();
  openCompare("/experiments/compare");
  expect(
    await screen.findByText("Pick two experiments to compare"),
  ).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Compare" })).toBeInTheDocument();
});

test("while the comparison loads, its page shows its header", async () => {
  serveComparison((url) =>
    url.endsWith(COMPARE_URL) ? new Promise(() => {}) : undefined,
  );
  openCompare();
  expect(
    await screen.findByRole("heading", { name: "Compare" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Experiments" })).toHaveAttribute(
    "href",
    "/experiments",
  );
});

test("a comparison that fails to load shows the error under its header", async () => {
  serveComparison((url) =>
    url.endsWith(COMPARE_URL) ? failure("store unavailable") : undefined,
  );
  openCompare();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Compare" })).toBeInTheDocument();
});

test("a refresh that fails keeps the comparison on screen and says why", async () => {
  let loads = 0;
  serveComparison((url) =>
    url.endsWith(COMPARE_URL) && ++loads > 1
      ? failure("store unavailable")
      : undefined,
  );
  openCompare();
  expect(await screen.findByText("refund-policy")).toBeInTheDocument();
  await act(refreshQueries);
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(screen.getByText("refund-policy")).toBeInTheDocument();
});
