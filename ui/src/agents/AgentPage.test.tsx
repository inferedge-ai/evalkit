// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route } from "react-router-dom";
import { afterEach, expect, test, vi } from "vitest";
import type { AgentInfo, MappingRecord, SessionTrajectory } from "../lib/types";
import { closePage, member, renderPage, stubServer } from "../testFixtures";
import { AgentPage } from "./AgentPage";

// The agent form imports a file outside ui/ that jsdom runs can't load.
vi.mock("./AgentForm", () => ({ AgentForm: () => null }));

const agent: AgentInfo = {
  id: 7,
  name: "bot",
  workspace: "dev",
  ref: "bot@dev",
  slug: "bot@dev",
  workspace_type: "shared",
  harness_url: null,
  trajectory_source: "atif",
  repo_url: null,
  settings: {
    max_concurrent_runs: 4,
    trajectory_wait_seconds: 30,
    run_timeout_seconds: 600,
    poll_interval_seconds: 2,
    stop_grace_seconds: 10,
    provides: null,
    trace_source: null,
    project: null,
    import_settle_seconds: 15,
    import_wait_seconds: 120,
  },
  driver: "sdk",
  archived: false,
  has_api_key: false,
};

const traced: AgentInfo = { ...agent, trajectory_source: "otel" };

const AGENT_URL = "/api/agents/bot%40dev";

const activeMapping: MappingRecord = {
  id: 3,
  version: 1,
  status: "active",
  document: {
    schema_version: "1",
    kind: {
      attribute: "gen_ai.operation.name",
      values: { chat: "llm", execute_tool: "tool", invoke_agent: "agent" },
    },
    session_id: { attribute: "gen_ai.conversation.id" },
    model: { attribute: "gen_ai.request.model" },
    tokens: {
      prompt: { attribute: "gen_ai.usage.input_tokens" },
      completion: { attribute: "gen_ai.usage.output_tokens" },
    },
    tool: { name: { attribute: "gen_ai.tool.name" } },
  },
  created_by: "akshat",
  created_at: "2026-11-09T10:00:00Z",
  activated_at: "2026-11-09T10:05:00Z",
};

const failure = (status: number, detail: string) =>
  Response.json({ detail }, { status });

const placeholders = (root: ParentNode = document) =>
  root.querySelectorAll('[data-slot="skeleton"]');

afterEach(closePage);

function serveAgent(served: AgentInfo) {
  const archives: string[] = [];
  stubServer((url, init) => {
    if (url.endsWith(`${AGENT_URL}/archive`)) {
      archives.push(init?.method ?? "GET");
      return Response.json({ ...served, archived: true });
    }
    if (url.endsWith(AGENT_URL)) return Response.json(served);
    if (url.includes(`${AGENT_URL}/runs?`))
      return Response.json({ runs: [], total: 0 });
  });
  return archives;
}

function openAgent(tab: string) {
  renderPage(
    member({ dev: ["writer"] }),
    `/agents/bot@dev/${tab}`,
    <Route path="/agents/:agent/:tab" element={<AgentPage />} />,
  );
}

test("archiving an agent asks first, and only archives once confirmed", async () => {
  const archives = serveAgent(agent);
  const user = userEvent.setup();
  openAgent("overview");

  await user.click(await screen.findByRole("button", { name: "Archive" }));
  const confirm = await screen.findByRole("alertdialog", {
    name: "Archive bot@dev?",
  });
  expect(confirm).toHaveAccessibleDescription(
    "Nobody can run it until it's restored. Its runs and results stay.",
  );
  await user.click(within(confirm).getByRole("button", { name: "Cancel" }));
  await waitFor(() =>
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(),
  );
  expect(archives).toEqual([]);

  await user.click(screen.getByRole("button", { name: "Archive" }));
  await user.click(
    within(await screen.findByRole("alertdialog")).getByRole("button", {
      name: "Archive",
    }),
  );
  expect(
    await screen.findByRole("button", { name: "Restore" }),
  ).toBeInTheDocument();
  expect(screen.getByText("archived")).toBeInTheDocument();
  expect(archives).toEqual(["POST"]);
});

test("the page header says how evalkit reaches the agent and where its trajectories come from", async () => {
  serveAgent(agent);
  openAgent("overview");
  const header = await screen.findByRole("banner");
  expect(
    await within(header).findByText("SDK · ATIF from the agent"),
  ).toBeInTheDocument();
});

test("a traced agent without a mapping or sessions says how to push a mapping and that there is nothing to preview", async () => {
  serveAgent(traced);
  openAgent("mappings");
  expect(await screen.findByText("No mapping yet")).toBeInTheDocument();
  expect(screen.getByText(/evalkit traces mapping push/)).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: /^Rules/ })).toBeNull();
  expect(
    await screen.findByText("No sessions to preview yet"),
  ).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "session" })).toBeNull();
});

test("a traced agent with an active mapping shows its rules", async () => {
  stubServer((url) => {
    if (url.endsWith(`${AGENT_URL}/mappings`))
      return Response.json([activeMapping]);
    if (url.endsWith(AGENT_URL)) return Response.json(traced);
    if (url.includes(`${AGENT_URL}/runs?`))
      return Response.json({ runs: [], total: 0 });
  });
  openAgent("mappings");
  expect(
    await screen.findByRole("heading", { name: "Rules: v1 (active)" }),
  ).toBeInTheDocument();
  expect(screen.getByText("gen_ai.conversation.id")).toBeInTheDocument();
  expect(screen.queryByText("No mapping yet")).toBeNull();
});

test("while the trace sources load, the Import tab does not say there are none", async () => {
  stubServer((url) => {
    if (url.endsWith("/api/trace-sources")) return new Promise(() => {});
    if (url.endsWith(AGENT_URL)) return Response.json(traced);
  });
  openAgent("import");
  expect(
    await screen.findByRole("heading", { name: "Range imports" }),
  ).toBeInTheDocument();
  expect(screen.queryByText(/No trace sources yet/)).toBeNull();
});

test("when the mapping versions fail to load, the Mappings tab shows the error instead of loading forever", async () => {
  stubServer((url) => {
    if (url.endsWith(`${AGENT_URL}/mappings`))
      return failure(500, "store unavailable");
    if (url.endsWith(AGENT_URL)) return Response.json(traced);
    if (url.includes(`${AGENT_URL}/runs?`))
      return Response.json({
        runs: [{ run_id: "s-1", title: null, user_id: null, created_at: null }],
        total: 1,
      });
  });
  openAgent("mappings");
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  await screen.findByRole("button", { name: "session" });
  expect(placeholders()).toHaveLength(0);
});

test("a draft preview that fails shows its error in the draft column, beside the active preview", async () => {
  const draftMapping: MappingRecord = {
    ...activeMapping,
    id: 4,
    version: 2,
    status: "draft",
    activated_at: null,
  };
  const preview: SessionTrajectory = {
    settled: true,
    root_present: true,
    span_count: 0,
    mapping_version: 1,
    unmapped_attributes: [],
    trajectory: null,
    esf: [],
  };
  stubServer((url) => {
    if (url.endsWith(`${AGENT_URL}/mappings`))
      return Response.json([draftMapping, activeMapping]);
    if (url.endsWith(AGENT_URL)) return Response.json(traced);
    if (url.includes(`${AGENT_URL}/runs?`))
      return Response.json({
        runs: [{ run_id: "s-1", title: null, user_id: null, created_at: null }],
        total: 1,
      });
    if (url.includes("mapping=draft"))
      return failure(404, "no draft mapping for this agent");
    if (url.includes("mapping=active")) return Response.json(preview);
  });
  openAgent("mappings");
  const draftColumn = (
    await screen.findByRole("heading", { name: "Draft mapping" })
  ).closest("section")!;
  expect(
    await within(draftColumn as HTMLElement).findByText(
      /no draft mapping for this agent/,
    ),
  ).toBeInTheDocument();
  expect(placeholders(draftColumn)).toHaveLength(0);
  const activeColumn = screen
    .getByRole("heading", { name: "Active mapping" })
    .closest("section")!;
  expect(
    await within(activeColumn as HTMLElement).findByText(
      "No spans in this session.",
    ),
  ).toBeInTheDocument();
});

test("when the range imports fail to load, the Import tab shows the error instead of loading forever", async () => {
  stubServer((url) => {
    if (url.endsWith(`${AGENT_URL}/imports`))
      return failure(500, "imports unavailable");
    if (url.endsWith(AGENT_URL)) return Response.json(traced);
  });
  openAgent("import");
  expect(await screen.findByText(/imports unavailable/)).toBeInTheDocument();
  const imports = screen
    .getByRole("heading", { name: "Range imports" })
    .closest("section")!;
  expect(placeholders(imports)).toHaveLength(0);
});

test("when the trace sources fail to load, the Import tab shows the error and does not say there are none", async () => {
  stubServer((url) => {
    if (url.endsWith("/api/trace-sources"))
      return Response.json({ detail: "store unavailable" }, { status: 500 });
    if (url.endsWith(AGENT_URL)) return Response.json(traced);
  });
  openAgent("import");
  expect(await screen.findByText(/store unavailable/)).toBeInTheDocument();
  expect(screen.queryByText(/No trace sources yet/)).toBeNull();
  const sources = screen
    .getByRole("heading", { name: "Import from a trace source" })
    .closest("section")!;
  expect(placeholders(sources)).toHaveLength(0);
});

test("when the trace sources and the range imports both fail, each section shows its own error", async () => {
  stubServer((url) => {
    if (url.endsWith("/api/trace-sources"))
      return failure(500, "sources unavailable");
    if (url.endsWith(`${AGENT_URL}/imports`))
      return failure(500, "imports unavailable");
    if (url.endsWith(AGENT_URL)) return Response.json(traced);
  });
  openAgent("import");
  const section = async (name: string) =>
    (await screen.findByRole("heading", { name })).closest(
      "section",
    ) as HTMLElement;
  expect(
    await within(await section("Import from a trace source")).findByText(
      /sources unavailable/,
    ),
  ).toBeInTheDocument();
  expect(
    await within(await section("Range imports")).findByText(
      /imports unavailable/,
    ),
  ).toBeInTheDocument();
});

test("importing traces without a trace source says how an admin adds one", async () => {
  serveAgent(traced);
  openAgent("import");
  expect(await screen.findByText("No trace sources yet")).toBeInTheDocument();
  expect(screen.getByText(/evalkit trace-sources create/)).toBeInTheDocument();
});
