// @vitest-environment jsdom
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test, vi } from "vitest";
import { queryClient } from "../lib/query";
import type { AgentInfo, MappingPreset } from "../lib/types";
import { closePage, stubServer } from "../testFixtures";
import { AgentForm } from "./AgentForm";

const preset: MappingPreset = {
  name: "gen_ai",
  document: {
    schema_version: "1",
    model: { attribute: "gen_ai.request.model" },
  },
};

Element.prototype.scrollIntoView = () => {};

afterEach(closePage);

async function choose(
  user: ReturnType<typeof userEvent.setup>,
  select: string,
  option: string,
) {
  const button = screen.getByRole("button", { name: select });
  await expect.poll(() => button.hasAttribute("disabled")).toBe(false);
  await user.click(button);
  await user.click(await screen.findByRole("option", { name: option }));
}

test("a preset picked in the new-agent form is the mapping the agent is created with", async () => {
  const created: Record<string, unknown>[] = [];
  stubServer((url, init) => {
    if (url.endsWith("/api/mapping-presets")) return Response.json([preset]);
    if (url.endsWith("/api/agents") && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return Response.json({ ref: "tracer@dev" });
    }
  });
  const onSaved = vi.fn<(agent: AgentInfo) => void>();
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <AgentForm
        workspace="dev"
        shared={false}
        onSaved={onSaved}
        onClose={() => {}}
      />
    </QueryClientProvider>,
  );

  await user.type(screen.getByLabelText("Name"), "tracer");
  await choose(user, "trajectory source", "From traces (OTel spans)");
  expect(screen.getByRole("button", { name: "Create agent" })).toBeDisabled();
  await choose(user, "mapping preset", "gen_ai");
  await user.click(screen.getByRole("button", { name: "Create agent" }));

  await expect.poll(() => onSaved.mock.calls.length).toBe(1);
  expect(created).toHaveLength(1);
  expect(created[0]).toMatchObject({
    workspace: "dev",
    name: "tracer",
    trajectory_source: "otel",
    mapping: preset.document,
  });
});

test("a Langfuse trace source takes a trace filter, linked to Langfuse's filter docs", async () => {
  const created: Record<string, unknown>[] = [];
  stubServer((url, init) => {
    if (url.endsWith("/api/mapping-presets")) return Response.json([preset]);
    if (url.endsWith("/api/trace-sources"))
      return Response.json([
        {
          id: 1,
          name: "lf",
          provider: "langfuse",
          url: "https://lf",
          archived: false,
        },
      ]);
    if (url.endsWith("/api/agents") && init?.method === "POST") {
      created.push(JSON.parse(String(init.body)));
      return Response.json({ ref: "tracer@dev" });
    }
  });
  const onSaved = vi.fn<(agent: AgentInfo) => void>();
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <AgentForm
        workspace="dev"
        shared={false}
        onSaved={onSaved}
        onClose={() => {}}
      />
    </QueryClientProvider>,
  );
  await user.type(screen.getByLabelText("Name"), "tracer");
  await choose(user, "trajectory source", "From traces (OTel spans)");
  await choose(user, "mapping preset", "gen_ai");
  expect(screen.getByRole("note")).toHaveTextContent(
    "Supported today: Phoenix and Langfuse",
  );
  expect(screen.queryByText(/x-langfuse-ingestion-version: 4/)).toBeNull();
  await choose(user, "trace source", "lf (langfuse)");
  expect(
    screen.getByText(/use the langfuse span mapping preset/),
  ).toHaveTextContent("x-langfuse-ingestion-version: 4");
  await user.type(screen.getByLabelText("Project"), "e2e-test");
  const filter =
    '[{"type": "string", "column": "traceName", "operator": "=", "value": "a"}]';
  await user.click(screen.getByLabelText("Trace filter"));
  await user.paste(filter);
  expect(
    screen.getByRole("link", { name: "How to write a Langfuse filter" }),
  ).toHaveAttribute("href", expect.stringContaining("langfuse.com"));
  await user.click(screen.getByRole("button", { name: "Create agent" }));
  await expect.poll(() => onSaved.mock.calls.length).toBe(1);
  expect(created[0]).toMatchObject({
    settings: { trace_source: "lf", project: "e2e-test", trace_filter: filter },
  });
});

test("a Phoenix trace source shows a Phoenix filter example and links to Phoenix's filter docs", async () => {
  stubServer((url) => {
    if (url.endsWith("/api/mapping-presets")) return Response.json([preset]);
    if (url.endsWith("/api/trace-sources"))
      return Response.json([
        {
          id: 1,
          name: "px",
          provider: "phoenix",
          url: "https://px",
          archived: false,
        },
      ]);
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={queryClient}>
      <AgentForm
        workspace="dev"
        shared={false}
        onSaved={() => {}}
        onClose={() => {}}
      />
    </QueryClientProvider>,
  );
  await choose(user, "trajectory source", "From traces (OTel spans)");
  await choose(user, "trace source", "px (phoenix)");
  expect(screen.getByLabelText("Trace filter")).toHaveAttribute(
    "placeholder",
    "optional, e.g. metadata['agent'] == 'my-agent'",
  );
  expect(
    screen.getByRole("link", { name: "How to write a Phoenix filter" }),
  ).toHaveAttribute("href", expect.stringContaining("arize.com/docs/phoenix"));
  expect(screen.queryByText(/x-langfuse-ingestion-version/)).toBeNull();
});
