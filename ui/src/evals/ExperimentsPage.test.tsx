// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import type { WorkspaceRole } from "../lib/types";
import {
  closePage,
  devWorkspace,
  failure,
  member,
  renderPage,
  stubServer,
} from "../testFixtures";
import { ExperimentsPage } from "./ExperimentsPage";

afterEach(closePage);

function openExperiments(roles: WorkspaceRole[] = ["writer"]) {
  renderPage(
    member({ dev: roles }),
    "/experiments",
    <Route path="/experiments" element={<ExperimentsPage />} />,
  );
}

test("a workspace with no experiments says how to start one", async () => {
  stubServer(() => undefined);
  openExperiments();
  expect(
    await screen.findByText("No experiments in @dev yet"),
  ).toBeInTheDocument();
  expect(
    screen.getByText("evalkit run <dataset>@dev --agent <agent>@dev"),
  ).toBeInTheDocument();
});

test("a reviewer, who may start experiments, is told how to start one", async () => {
  stubServer(() => undefined);
  openExperiments(["reviewer"]);
  expect(
    await screen.findByText("evalkit run <dataset>@dev --agent <agent>@dev"),
  ).toBeInTheDocument();
});

test("someone who is not a member is told the workspace is read-only for them", async () => {
  stubServer(() => undefined);
  openExperiments([]);
  expect(
    await screen.findByText("This workspace is read-only for you."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Start one with Run experiment/)).toBeNull();
});

test("an archived workspace says it is archived instead of how to start one", async () => {
  stubServer((url) =>
    url.endsWith("/api/workspaces?include_archived=true")
      ? Response.json([{ ...devWorkspace, archived: true }])
      : undefined,
  );
  openExperiments();
  expect(
    await screen.findByText("This workspace is archived."),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Start one with Run experiment/)).toBeNull();
});

test("when the experiments cannot load, the page shows the error, not an empty list", async () => {
  stubServer((url) =>
    url.includes("/api/experiments?")
      ? failure("store unavailable")
      : undefined,
  );
  openExperiments();
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(screen.queryByText("0 experiments")).toBeNull();
  await expect(
    screen.findByText(/No experiments/, {}, { timeout: 300 }),
  ).rejects.toThrow();
});
