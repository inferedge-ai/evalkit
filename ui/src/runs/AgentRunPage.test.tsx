// @vitest-environment jsdom
import { act, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import fixture from "../lib/atif_full.fixture.json";
import { refreshQueries } from "../lib/query";
import type { AtifTrajectory, RunList } from "../lib/types";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { AgentRunPage } from "./AgentRunPage";

const RUN_URL = "/api/agents/bot%40dev/runs/run-abc123";

const experimentRun: RunList = {
  runs: [
    {
      run_id: "run-abc123",
      title: "ACME FY24 results",
      user_id: null,
      created_at: "2026-09-01T10:00:00Z",
      experiment_id: 7,
      experiment_number: 3,
      case_id: "acme-revenue",
    },
  ],
  total: 1,
};

const untimed: AtifTrajectory = {
  schema_version: "ATIF-v1.8",
  session_id: "run-abc123",
  agent: { name: "myagent" },
  steps: [{ step_id: 1, source: "user", message: "Hello" }],
} as unknown as AtifTrajectory;

afterEach(closePage);

function serveRun(
  route: (url: string) => Response | undefined = () => undefined,
) {
  stubServer((url) => {
    const answer = route(url);
    if (answer) return answer;
    if (url.endsWith(`${RUN_URL}/trajectory`)) return Response.json(fixture);
    if (url.includes("/api/agents/bot%40dev/runs?"))
      return Response.json(experimentRun);
  });
}

function openRun() {
  renderPage(
    member({ dev: ["writer"] }),
    "/runs/bot@dev/run-abc123",
    <Route path="/runs/:agent/:runId" element={<AgentRunPage />} />,
  );
}

test("a run from an experiment links to its experiment run from the page header", async () => {
  serveRun();
  openRun();
  const header = await screen.findByRole("banner");
  expect(
    await within(header).findByRole("link", { name: /Experiment run/ }),
  ).toHaveAttribute("href", "/experiments/7/acme-revenue");
});

test("a refresh that fails keeps the trajectory on screen with the error above it", async () => {
  let down = false;
  serveRun((url) =>
    down && url.endsWith(`${RUN_URL}/trajectory`)
      ? Response.json({ detail: "harness unreachable" }, { status: 502 })
      : undefined,
  );
  openRun();
  expect((await screen.findAllByText("web_search")).length).toBeGreaterThan(0);
  down = true;
  await act(() => refreshQueries());
  expect(await screen.findByText(/harness unreachable/)).toBeInTheDocument();
  expect(screen.getAllByText("web_search").length).toBeGreaterThan(0);
});

test("a trajectory without step times says what the timing tab needs", async () => {
  serveRun((url) =>
    url.endsWith(`${RUN_URL}/trajectory`) ? Response.json(untimed) : undefined,
  );
  openRun();
  await userEvent.click(await screen.findByRole("tab", { name: "Timing" }));
  expect(
    await screen.findByText("No step timestamps in this trajectory"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("Timing needs at least two timestamped steps."),
  ).toBeInTheDocument();
});

test("a run whose trajectory fails to load shows the error and stops showing placeholders", async () => {
  serveRun((url) => {
    if (url.endsWith(`${RUN_URL}/trajectory`))
      return Response.json({ detail: "harness unreachable" }, { status: 502 });
    if (url.includes("/api/agents/bot%40dev/runs?"))
      return Response.json({ runs: [], total: 0 });
  });
  openRun();
  expect(await screen.findByText(/harness unreachable/)).toBeInTheDocument();
  expect(document.querySelectorAll('[data-slot="skeleton"]')).toHaveLength(0);
});

test("a workspace refresh that fails keeps the files on screen with the error above them", async () => {
  let down = false;
  serveRun((url) =>
    url.endsWith(`${RUN_URL}/files`)
      ? down
        ? Response.json({ detail: "harness unreachable" }, { status: 502 })
        : Response.json({ files: [{ path: "report.md", size: 120 }] })
      : undefined,
  );
  openRun();
  await userEvent.click(await screen.findByRole("tab", { name: "Workspace" }));
  expect(await screen.findByText("report.md")).toBeInTheDocument();
  down = true;
  await act(() => refreshQueries());
  expect(await screen.findByText(/harness unreachable/)).toBeInTheDocument();
  expect(screen.getByText("report.md")).toBeInTheDocument();
});

const harnessError = () =>
  Response.json({ detail: "harness error" }, { status: 502 });
const proxyPage = () =>
  new Response("<html><body>Bad Gateway</body></html>", {
    status: 502,
    headers: { "content-type": "text/html" },
  });
const neverStored = () =>
  Response.json({ detail: "no such file" }, { status: 404 });
const notOnHarness = () =>
  Response.json(
    {
      detail:
        "not found on the harness: wrong path, or the harness no longer has it",
    },
    { status: 404 },
  );

test.each([
  { file: "notes.txt", answer: harnessError, says: "harness error" },
  { file: "chart.png", answer: harnessError, says: "harness error" },
  { file: "notes.txt", answer: proxyPage, says: "HTTP 502" },
  { file: "chart.png", answer: proxyPage, says: "HTTP 502" },
  {
    file: "notes.txt",
    answer: notOnHarness,
    says: "The harness doesn't have this file",
  },
  {
    file: "chart.png",
    answer: notOnHarness,
    says: "The harness doesn't have this file",
  },
  { file: "notes.txt", answer: neverStored, says: "No such file" },
  { file: "chart.png", answer: neverStored, says: "No such file" },
])(
  "$file that fails to load says why: $says",
  async ({ file, answer, says }) => {
    serveRun((url) => {
      if (url.endsWith(`${RUN_URL}/files`))
        return Response.json({
          files: [
            { path: "notes.txt", size: 10 },
            { path: "chart.png", size: 100 },
          ],
        });
      if (url.endsWith(`${RUN_URL}/files/${file}`)) return answer();
    });
    openRun();
    await userEvent.click(
      await screen.findByRole("tab", { name: "Workspace" }),
    );
    await userEvent.click(await screen.findByText(file));
    expect(await screen.findByText(new RegExp(`^${says}`))).toBeInTheDocument();
  },
);

test("a run whose workspace holds no files says so", async () => {
  serveRun((url) =>
    url.endsWith(`${RUN_URL}/files`) ? Response.json({ files: [] }) : undefined,
  );
  openRun();
  await userEvent.click(await screen.findByRole("tab", { name: "Workspace" }));
  expect(
    await screen.findByText("No files in this run's workspace"),
  ).toBeInTheDocument();
});
