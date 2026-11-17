// @vitest-environment jsdom
import { fireEvent, screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import type { AgentSettings } from "../lib/types";
import { ImportPanel } from "./ImportPanel";

const settings: AgentSettings = {
  max_concurrent_runs: 4,
  trajectory_wait_seconds: 60,
  run_timeout_seconds: 840,
  poll_interval_seconds: 5,
  stop_grace_seconds: 120,
  provides: null,
  trace_source: "phx-prod",
  project: "research-agent",
  trace_filter: null,
  import_settle_seconds: 15,
  import_wait_seconds: 120,
};

afterEach(closePage);

test("the import form shows the agent's trace source, project and filter, and posts only the window", async () => {
  const posted: Record<string, unknown>[] = [];
  stubServer(async (url, init) => {
    if (url.includes("/imports") && init?.method === "POST") {
      posted.push(JSON.parse(String(init.body)));
      return Response.json({});
    }
    if (url.includes("/imports")) return Response.json([]);
  });
  renderPage(
    member({ dev: ["writer"] }),
    "/import",
    <Route
      path="/import"
      element={
        <ImportPanel
          agent="bot@dev"
          settings={{ ...settings, trace_filter: '[{"column": "name"}]' }}
        />
      }
    />,
  );
  expect(await screen.findByText("phx-prod")).toBeInTheDocument();
  expect(screen.getByText("research-agent")).toBeInTheDocument();
  expect(screen.getByText('[{"column": "name"}]')).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "trace source" })).toBeNull();
  fireEvent.change(screen.getByLabelText("Since"), {
    target: { value: "2026-11-01T00:00" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Start import" }));
  await expect.poll(() => posted.length).toBe(1);
  expect(Object.keys(posted[0]).sort()).toEqual(["since", "until"]);
});

test("an import shows how much is done and, when it failed, how many traces came in", async () => {
  const failed = {
    id: 7,
    agent: "bot@dev",
    source: "lf",
    project: "research-agent",
    since: "2026-11-01T00:00:00Z",
    until: "2026-11-05T00:00:00Z",
    resume_from: "2026-11-04T00:00:00Z",
    trace_filter: null,
    status: "failed",
    progress: 0.25,
    traces: 40,
    spans: 800,
    sample_session: null,
    error: "HTTPStatusError: 500",
    created_by: "admin",
    created_at: "2026-11-05T00:00:00Z",
    finished_at: "2026-11-05T00:01:00Z",
  };
  stubServer(async (url) => {
    if (url.includes("/trace-sources")) return Response.json([]);
    if (url.includes("/imports")) return Response.json([failed]);
  });
  renderPage(
    member({ dev: ["writer"] }),
    "/import",
    <Route
      path="/import"
      element={<ImportPanel agent="bot@dev" settings={settings} />}
    />,
  );
  expect(
    await screen.findByText("25% completed · 40 traces · 800 spans"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("HTTPStatusError: 500. 40 traces imported."),
  ).toBeInTheDocument();
});
