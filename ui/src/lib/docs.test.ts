import { describe, expect, it } from "vitest";
import { DOC_GROUPS, DOC_TABS, docRoute, fillTemplate, isDocTab } from "./docs";

describe("docs", () => {
  it("lists the pages in order, grouped", () => {
    expect(DOC_TABS.map((t) => t.id)).toEqual([
      "quickstart",
      "concepts",
      "try-locally",
      "install",
      "login",
      "config",
      "workspaces",
      "sdk-runs",
      "tracing",
      "mappings",
      "harness-api",
      "mcp",
    ]);
    expect(DOC_GROUPS).toEqual(["Get started", "Traces", "Harness API", "MCP"]);
    expect(isDocTab("tracing")).toBe(true);
    expect(isDocTab("nope")).toBe(false);
  });

  it("fills server values into a template", () => {
    const out = fillTemplate(
      'evalkit login --url {{origin}} # "evalkit-py=={{version}}"',
      { origin: "https://e", version: "0.9.0" },
    );
    expect(out).toBe('evalkit login --url https://e # "evalkit-py==0.9.0"');
  });

  it("every tab body has a heading", () => {
    for (const tab of DOC_TABS) expect(tab.body.startsWith("# ")).toBe(true);
  });

  it("maps links between pages to app routes", () => {
    expect(docRoute("tracing.md")).toBe("/docs/tracing");
    expect(docRoute("sdk-runs.md#the-task")).toBe("/docs/sdk-runs#the-task");
    expect(docRoute("nope.md")).toBeNull();
    expect(docRoute("https://example.com/a.md")).toBeNull();
    expect(docRoute(undefined)).toBeNull();
  });

  it("every link to another .md page names a page the UI shows", () => {
    for (const tab of DOC_TABS) {
      for (const [, href] of tab.body.matchAll(
        /\]\(([^)\s]+\.md(?:#[^)]*)?)\)/g,
      )) {
        expect(docRoute(href), `${tab.id} links ${href}`).not.toBeNull();
      }
    }
  });
});
