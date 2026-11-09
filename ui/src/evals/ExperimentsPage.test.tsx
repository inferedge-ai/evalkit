// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import {
  closePage,
  failure,
  member,
  renderPage,
  stubServer,
} from "../testFixtures";
import { ExperimentsPage } from "./ExperimentsPage";

afterEach(closePage);

function openExperiments() {
  renderPage(
    member({ dev: ["writer"] }),
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
