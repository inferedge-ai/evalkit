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
