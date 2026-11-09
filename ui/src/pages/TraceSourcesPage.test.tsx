// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import type { Me, TraceSourceInfo } from "../lib/types";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { TraceSourcesPage } from "./TraceSourcesPage";

const phoenix: TraceSourceInfo = {
  id: 1,
  name: "phoenix-prod",
  provider: "phoenix",
  url: "https://phoenix.example.com",
  archived: false,
};

afterEach(closePage);

function openTraceSources(me: Me) {
  stubServer((url) =>
    url.endsWith("/api/trace-sources") ? Response.json([phoenix]) : undefined,
  );
  renderPage(
    me,
    "/settings/trace-sources",
    <Route path="/settings/trace-sources" element={<TraceSourcesPage />} />,
  );
}

test("a member reads the trace sources without controls the server would refuse", async () => {
  openTraceSources(member({ dev: ["writer"] }));
  expect(await screen.findByText("phoenix-prod")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Add trace source" })).toBeNull();
  expect(screen.queryByRole("button", { name: "archive" })).toBeNull();
});

test("an admin adds and archives trace sources", async () => {
  openTraceSources({ ...member({}), is_admin: true });
  expect(await screen.findByText("phoenix-prod")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Add trace source" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "archive" })).toBeInTheDocument();
});
