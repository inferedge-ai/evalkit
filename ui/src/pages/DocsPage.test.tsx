// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import { DocsPage } from "./DocsPage";

afterEach(() => {
  queryClient.clear();
  vi.unstubAllGlobals();
});

function openDocs(path: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ version: "0.4.0", package: "evalkit" })),
  );
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/docs/:tab" element={<DocsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// The docs pages are long; whole-document role queries take minutes in jsdom.
test("on a phone the docs menu folds under the page's name and folds again after a pick", async () => {
  openDocs("/docs/mcp");
  const user = userEvent.setup();
  const nav = within(document.querySelector("nav")!);
  const menu = nav.getByRole("button", { name: /MCP/ });
  expect(menu).toHaveAttribute("aria-expanded", "false");

  await user.click(menu);
  expect(menu).toHaveAttribute("aria-expanded", "true");
  await user.click(nav.getByRole("link", { name: "Install" }));

  expect(
    await screen.findByText("Install", { selector: "h1" }),
  ).toBeInTheDocument();
  expect(nav.getByRole("button", { name: /Install/ })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
});
