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

function openTraceSources(me: Me, sources = [phoenix]) {
  stubServer((url) =>
    url.endsWith("/api/trace-sources") ? Response.json(sources) : undefined,
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

test("an admin sees the add form and archive buttons", async () => {
  openTraceSources({ ...member({}), is_admin: true });
  expect(await screen.findByText("phoenix-prod")).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "Add trace source" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "archive" })).toBeInTheDocument();
});

test("without trace sources, an admin is told how to add one", async () => {
  openTraceSources({ ...member({}), is_admin: true }, []);
  expect(await screen.findByText("No trace sources")).toBeInTheDocument();
  expect(screen.getByText(/Add a Phoenix server above/)).toBeInTheDocument();
});

test("a trace source list that fails without a message still says which error came back", async () => {
  stubServer((url) =>
    url.endsWith("/api/trace-sources")
      ? new Response("<html>Bad Gateway</html>", { status: 502 })
      : undefined,
  );
  renderPage(
    member({ dev: ["writer"] }),
    "/settings/trace-sources",
    <Route path="/settings/trace-sources" element={<TraceSourcesPage />} />,
  );
  expect(await screen.findByText("HTTP 502")).toBeInTheDocument();
});

test("without trace sources, a member is told an admin adds them", async () => {
  openTraceSources(member({ dev: ["writer"] }), []);
  expect(await screen.findByText("No trace sources")).toBeInTheDocument();
  expect(screen.getByText(/An admin adds them with/)).toBeInTheDocument();
  expect(screen.getByText("evalkit trace-sources create")).toBeInTheDocument();
});
