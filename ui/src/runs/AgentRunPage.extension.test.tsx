// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import fixture from "../lib/atif_full.fixture.json";
import type { RunRef } from "../lib/extensions";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { AgentRunPage } from "./AgentRunPage";

vi.mock("../extensions", () => ({
  extensions: {
    routes: [],
    publicRoutes: [],
    landingPage: null,
    nav: [],
    accountSettings: [],
    organizationSettings: [],
    useRunSidebar: ({ agent, runId }: RunRef) => [
      {
        title: "Notes",
        items: [{ key: "n", label: `${runId} of ${agent}`, stepId: 1 }],
      },
    ],
  },
}));

afterEach(closePage);

test("a package's sidebar sections show on the run page, given the agent and the run", async () => {
  stubServer((url) => {
    if (url.endsWith("/trajectory")) return Response.json(fixture);
  });
  renderPage(
    member({ dev: ["writer"] }),
    "/runs/bot%40dev/run-abc123",
    <Route path="/runs/:agent/:runId" element={<AgentRunPage />} />,
  );

  expect(
    await screen.findByRole("button", { name: /run-abc123 of bot@dev/ }),
  ).toBeVisible();
  expect(screen.getByText("Notes")).toBeVisible();
});
