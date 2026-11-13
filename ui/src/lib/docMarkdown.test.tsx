// @vitest-environment jsdom
import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import {
  remarkDocMarkers,
  resolveDocImage,
  slugifyHeading,
  slugifyRow,
} from "./docMarkdown";

function renderDoc(markdown: string) {
  return render(
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkDocMarkers]}>
      {markdown}
    </ReactMarkdown>,
  ).container;
}

describe("slugifyRow", () => {
  it.each([
    ["Case", "case"],
    ["App run", "app-run"],
    ["Trace, span, session", "trace-span-session"],
    ["evalkit.yaml", "evalkit-yaml"],
  ])("%s → %s", (text, slug) => {
    expect(slugifyRow(text)).toBe(slug);
  });
});

describe("slugifyHeading", () => {
  it.each([
    ["No trace history?", "no-trace-history"],
    ["An agent in another process", "an-agent-in-another-process"],
    ["The CLI, as a global tool", "the-cli-as-a-global-tool"],
    ["Config: evalkit.yaml", "config-evalkityaml"],
    ["required_tools_called", "required_tools_called"],
    [
      "1. Pick how evalkit reaches your agent",
      "1-pick-how-evalkit-reaches-your-agent",
    ],
  ])("%s → %s", (text, slug) => {
    expect(slugifyHeading(text)).toBe(slug);
  });
});

describe("heading ids", () => {
  it("gives headings MkDocs ids, numbers repeats and skips tab labels", () => {
    const container = renderDoc(
      [
        "## Config: `evalkit.yaml`",
        "## Setup",
        "## Setup",
        "<!-- tabs -->",
        "#### CLI",
        "",
        "text",
        "<!-- /tabs -->",
      ].join("\n"),
    );
    expect([...container.querySelectorAll("h2")].map((h) => h.id)).toEqual([
      "config-evalkityaml",
      "setup",
      "setup_1",
    ]);
    expect(container.querySelectorAll("[id='cli']")).toHaveLength(0);
  });
});

describe("remarkDocMarkers", () => {
  it("slugs the rendered text of a first cell with a link or emphasis", () => {
    const container = renderDoc(
      [
        "<!-- anchor-rows -->",
        "| A | B |",
        "| -- | -- |",
        "| [App run](runs.md#x) | 1 |",
        "| *Trace* **source** | 2 |",
      ].join("\n"),
    );
    expect([...container.querySelectorAll("tr[id]")].map((r) => r.id)).toEqual([
      "app-run",
      "trace-source",
    ]);
  });

  it("groups each #### heading between the markers into a tab", () => {
    const container = renderDoc(
      [
        "#### Outside",
        "",
        "<!-- tabs -->",
        "#### CLI",
        "",
        "```bash",
        "#### not a tab",
        "```",
        "",
        "#### UI",
        "",
        "Open the page.",
        "<!-- /tabs -->",
      ].join("\n"),
    );
    const tabs = container.querySelectorAll("[data-doc-tab]");
    expect([...tabs].map((t) => t.getAttribute("data-doc-tab"))).toEqual([
      "CLI",
      "UI",
    ]);
    expect(tabs[0].querySelector("code")?.textContent).toContain(
      "#### not a tab",
    );
    expect(tabs[1].textContent).toContain("Open the page.");
    expect(container.querySelector("h4")?.textContent).toBe("Outside");
  });

  it("gives each body row of a marked table an id from its first cell", () => {
    const container = renderDoc(
      [
        "<!-- anchor-rows -->",
        "| Term | Meaning |",
        "| -- | -- |",
        "| App run | One |",
        "| `evalkit.yaml` | Two |",
        "",
        "| Plain | Table |",
        "| -- | -- |",
        "| Case | Three |",
      ].join("\n"),
    );
    const ids = [...container.querySelectorAll("tr[id]")].map((r) => r.id);
    expect(ids).toEqual(["app-run", "evalkit-yaml"]);
  });
});

describe("resolveDocImage", () => {
  const images = { "../../../docs/user/images/run.png": "/assets/run-abc.png" };

  it("maps images/<file> to the bundled asset", () => {
    expect(resolveDocImage("images/run.png", images)).toBe(
      "/assets/run-abc.png",
    );
  });

  it("leaves other sources alone", () => {
    expect(resolveDocImage("https://x.test/a.png", images)).toBe(
      "https://x.test/a.png",
    );
    expect(resolveDocImage("images/missing.png", images)).toBe(
      "images/missing.png",
    );
  });
});
