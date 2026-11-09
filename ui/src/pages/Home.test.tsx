// @vitest-environment jsdom
import { screen } from "@testing-library/react";
import { Route } from "react-router-dom";
import { afterEach, expect, test } from "vitest";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { Home } from "./Home";

afterEach(closePage);

test("when the datasets cannot load, the home page shows the error, not an empty workspace", async () => {
  stubServer(async (url) => {
    if (url.includes("/api/agents?")) return Response.json([]);
    if (url.includes("/api/datasets?")) {
      await new Promise((settled) => setTimeout(settled, 50));
      return Response.json({ detail: "store unavailable" }, { status: 500 });
    }
  });
  renderPage(
    member({ dev: ["writer"] }),
    "/",
    <Route path="/" element={<Home />} />,
  );
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(screen.queryByText(/No datasets or agents/)).toBeNull();
});
