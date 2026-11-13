// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  MemoryRouter,
  Route,
  Routes,
  useNavigate,
  type NavigateFunction,
} from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import { closePage } from "../testFixtures";
import { DocsPage } from "./DocsPage";

afterEach(closePage);

// Stands in for the browser's Back button.
let navigateTo: NavigateFunction = () => {};
function History() {
  navigateTo = useNavigate();
  return null;
}

function openDocs(path: string) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json({ version: "0.4.0", package: "evalkit" })),
  );
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <History />
        <Routes>
          <Route path="/docs/:tab" element={<DocsPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("a docs command that wraps onto a second line keeps its shell variables as written", async () => {
  openDocs("/docs/ci-sync");
  await screen.findByText("Sync cases from CI", { selector: "h1" });
  const login = [...document.querySelectorAll("article p > code")].find((c) =>
    c.textContent?.includes("evalkit login"),
  );
  expect(login?.textContent).toContain('"$EVALKIT_SERVER_URL"');
  expect(login?.textContent).not.toContain("\\$");
});

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

test("going back after picking a docs page leaves the menu folded", async () => {
  openDocs("/docs/mcp");
  const user = userEvent.setup();
  const nav = within(document.querySelector("nav")!);
  await user.click(nav.getByRole("button", { name: /MCP/ }));
  await user.click(nav.getByRole("link", { name: "Install" }));
  await screen.findByText("Install", { selector: "h1" });

  act(() => navigateTo(-1));

  expect(
    await screen.findByText("MCP", { selector: "h1" }),
  ).toBeInTheDocument();
  expect(nav.getByRole("button", { name: /MCP/ })).toHaveAttribute(
    "aria-expanded",
    "false",
  );
});
