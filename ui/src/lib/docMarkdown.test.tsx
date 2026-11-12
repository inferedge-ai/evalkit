// @vitest-environment jsdom
import { render } from "@testing-library/react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { describe, expect, it } from "vitest";
import { Admonition } from "@/components/widgets/Admonition";
import {
  prepareDocMarkdown,
  remarkDocMarkers,
  resolveDocImage,
  slugifyHeading,
  slugifyRow,
} from "./docMarkdown";

function renderDoc(markdown: string) {
  return render(
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkDocMarkers]}>
      {prepareDocMarkdown(markdown)}
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

describe("admonitions", () => {
  it("takes a custom title and keeps lists and code fences in the body", () => {
    const container = renderDoc(
      [
        '!!! warning "Before you sync"',
        "    Check two things:",
        "",
        "    - the `cases` folder",
        "    - the [config](config.md)",
        "",
        "    ```bash",
        "    !!! note not a marker",
        "    evalkit sync",
        "    ```",
        "",
        "After.",
      ].join("\n"),
    );
    const box = container.querySelector("[data-admonition]");
    expect(box?.getAttribute("data-admonition")).toBe("warning");
    expect(box?.getAttribute("data-title")).toBe("Before you sync");
    expect(box?.querySelectorAll("li")).toHaveLength(2);
    expect(box?.querySelector("pre code")?.textContent).toContain(
      "!!! note not a marker",
    );
    expect(container.querySelector("p:last-child")?.textContent).toBe("After.");
  });

  it.each([
    ["note", "note", "Note"],
    ["tip", "tip", "Tip"],
    ["info", "info", "Info"],
    ["danger", "danger", "Danger"],
    ["caution", "warning", "Caution"],
    ["mystery", "note", "Mystery"],
  ])("!!! %s is a %s box titled %s", (type, kind, title) => {
    const box = renderDoc(`!!! ${type}\n    Body.`).querySelector(
      "[data-admonition]",
    );
    expect(box?.getAttribute("data-admonition")).toBe(kind);
    expect(box?.getAttribute("data-title")).toBe(title);
    expect(box?.textContent).toBe("Body.");
  });

  it("works inside a tab block", () => {
    const container = renderDoc(
      [
        "<!-- tabs -->",
        "#### CLI",
        "",
        "!!! tip",
        "    Inside a tab.",
        "",
        "#### UI",
        "",
        "Other.",
        "<!-- /tabs -->",
      ].join("\n"),
    );
    const tab = container.querySelector("[data-doc-tab='CLI']");
    expect(tab?.querySelector("[data-admonition='tip']")?.textContent).toBe(
      "Inside a tab.",
    );
  });

  it("leaves a marker inside a code fence alone", () => {
    const container = renderDoc(
      ["```markdown", "!!! note", "    Body.", "```"].join("\n"),
    );
    expect(container.querySelector("[data-admonition]")).toBeNull();
    expect(container.querySelector("code")?.textContent).toContain("!!! note");
  });
});

describe("Admonition", () => {
  it("renders the title, the body and the kind's icon", () => {
    const { container, getByRole } = render(
      <Admonition kind="danger" type="bug" title="Known bug">
        <p>Body text.</p>
      </Admonition>,
    );
    const box = getByRole("note");
    expect(box.textContent).toContain("Known bug");
    expect(box.textContent).toContain("Body text.");
    expect(box.className).toContain("border-l-destructive");
    expect(container.querySelector("svg.text-destructive")).not.toBeNull();
  });

  it("leaves out an empty title", () => {
    const { container } = render(
      <Admonition kind="note" type="note" title="">
        <p>Only a body.</p>
      </Admonition>,
    );
    expect(container.querySelector("[data-slot=alert-title]")).toBeNull();
  });
});
