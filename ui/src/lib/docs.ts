import quickstart from "../../../docs/user/quickstart.md?raw";
import install from "../../../docs/user/install.md?raw";
import login from "../../../docs/user/login.md?raw";
import config from "../../../docs/user/config.md?raw";
import workspaces from "../../../docs/user/workspaces.md?raw";
import sdkRuns from "../../../docs/user/sdk-runs.md?raw";
import tracing from "../../../docs/user/tracing.md?raw";
import mappings from "../../../docs/user/mappings.md?raw";
import harnessApi from "../../../docs/user/harness-api.md?raw";
import tryLocally from "../../../docs/user/try-locally.md?raw";
import concepts from "../../../docs/user/concepts.md?raw";
import mcp from "../../../docs/user/mcp.md?raw";

export const DOC_TABS = [
  {
    id: "quickstart",
    label: "Quickstart",
    group: "Get started",
    body: quickstart,
  },
  { id: "concepts", label: "Concepts", group: "Get started", body: concepts },
  {
    id: "try-locally",
    label: "Try locally",
    group: "Get started",
    body: tryLocally,
  },
  { id: "install", label: "Install", group: "Get started", body: install },
  { id: "login", label: "Login", group: "Get started", body: login },
  { id: "config", label: "Config", group: "Get started", body: config },
  {
    id: "workspaces",
    label: "Workspaces",
    group: "Get started",
    body: workspaces,
  },
  { id: "sdk-runs", label: "SDK runs", group: "Traces", body: sdkRuns },
  { id: "tracing", label: "Tracing", group: "Traces", body: tracing },
  { id: "mappings", label: "Mappings", group: "Traces", body: mappings },
  {
    id: "harness-api",
    label: "Harness API",
    group: "Harness API",
    body: harnessApi,
  },
  { id: "mcp", label: "MCP", group: "MCP", body: mcp },
] as const;

export const DOC_GROUPS = [...new Set(DOC_TABS.map((t) => t.group))];

export type DocTab = (typeof DOC_TABS)[number]["id"];

export type DocValues = Record<"origin" | "version", string>;

export function isDocTab(value: string | undefined): value is DocTab {
  return DOC_TABS.some((t) => t.id === value);
}

// Every tab is a markdown file with {{origin}} and {{version}} placeholders filled from GET /api/cli, so snippets paste as-is.
export function fillTemplate(markdown: string, values: DocValues): string {
  return markdown.replace(
    /\{\{(origin|version)\}\}/g,
    (_, key: keyof DocValues) => values[key],
  );
}

// Pages link to each other as `tracing.md` so they also work as files on
// GitHub; in the app those links become /docs/<id> routes.
export function docRoute(href: string | undefined): string | null {
  const match = /^([\w-]+)\.md(#.*)?$/.exec(href ?? "");
  if (!match || !isDocTab(match[1])) return null;
  return `/docs/${match[1]}${match[2] ?? ""}`;
}
