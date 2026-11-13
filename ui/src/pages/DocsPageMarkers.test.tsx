// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import { closePage } from "../testFixtures";
import { DocsPage } from "./DocsPage";

vi.mock("../lib/docs", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/docs")>();
  const bodies: Record<string, string> = {
    concepts: [
      "# Concepts",
      "",
      "<!-- anchor-rows -->",
      "| Term | Meaning |",
      "| -- | -- |",
      "| Case | One question. |",
      "| App run | A run outside an experiment. |",
    ].join("\n"),
    install: [
      "# Install",
      "",
      "See [the global tool](#the-cli-as-a-global-tool).",
      "",
      "<!-- tabs -->",
      "#### CLI",
      "",
      "Run the command.",
      "",
      "#### UI",
      "",
      "Open the page.",
      "<!-- /tabs -->",
      "",
      "## The CLI, as a global tool",
      "",
      "Text.",
    ].join("\n"),
  };
  return {
    ...actual,
    DOC_TABS: actual.DOC_TABS.map((tab) => ({
      ...tab,
      body: bodies[tab.id] ?? tab.body,
    })),
  };
});

const scrollIntoView = vi.fn();

beforeEach(() => {
  scrollIntoView.mockClear();
  Element.prototype.scrollIntoView = scrollIntoView;
});

afterEach(closePage);

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

test("opening a row's anchor marks the row and scrolls to it", async () => {
  openDocs("/docs/concepts#app-run");
  const row = await screen.findByText("A run outside an experiment.");
  const tr = row.closest("tr")!;
  expect(tr).toHaveAttribute("id", "app-run");
  expect(tr).toHaveAttribute("data-targeted");
  expect(scrollIntoView.mock.contexts).toContain(tr);
});

test("a same-page heading link scrolls to the heading", async () => {
  openDocs("/docs/install");
  const user = userEvent.setup();
  const heading = await screen.findByText("The CLI, as a global tool", {
    selector: "h2",
  });
  expect(scrollIntoView).not.toHaveBeenCalled();

  await user.click(screen.getByRole("link", { name: "the global tool" }));

  expect(scrollIntoView.mock.contexts).toContain(heading);
});

test("a tab block renders as tabs and switching shows the other panel", async () => {
  openDocs("/docs/install");
  const user = userEvent.setup();
  const tablist = await screen.findByRole("tablist");
  const cli = within(tablist).getByRole("tab", { name: "CLI" });
  const ui = within(tablist).getByRole("tab", { name: "UI" });
  expect(cli).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Run the command.")).toBeVisible();

  await user.click(ui);

  expect(ui).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("Open the page.")).toBeVisible();
  expect(screen.queryByText("Run the command.")).not.toBeInTheDocument();
});

test("the quickstart's image points at the bundled file, not at the docs-relative path", async () => {
  openDocs("/docs/quickstart");
  const image = await screen.findByAltText(
    "Look at all the things I'm not doing",
  );
  const src = image.getAttribute("src") ?? "";
  expect(src).toContain("look-at-all-the-things-im-not-doing");
  expect(src.startsWith("images/")).toBe(false);
});
