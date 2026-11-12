// @vitest-environment jsdom
import { useState } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, test } from "vitest";
import { queryClient } from "../lib/query";
import type { MappingPreset } from "../lib/types";
import { closePage, stubServer } from "../testFixtures";
import { MappingPresetPicker } from "./MappingPresetPicker";

const presets: MappingPreset[] = [
  {
    name: "gen_ai",
    document: {
      schema_version: "1",
      model: { attribute: "gen_ai.request.model" },
    },
  },
  {
    name: "openinference",
    document: { schema_version: "1", model: { attribute: "llm.model_name" } },
  },
];

function Harness() {
  const [mapping, setMapping] = useState("");
  return (
    <>
      <MappingPresetPicker value={mapping} onChange={setMapping} />
      <textarea
        aria-label="span mapping"
        value={mapping}
        onChange={(e) => setMapping(e.target.value)}
      />
    </>
  );
}

function renderPicker() {
  stubServer((url) => {
    if (url.endsWith("/api/mapping-presets")) return Response.json(presets);
  });
  render(
    <QueryClientProvider client={queryClient}>
      <Harness />
    </QueryClientProvider>,
  );
}

async function pick(user: ReturnType<typeof userEvent.setup>, name: string) {
  const picker = screen.getByRole("button", { name: "mapping preset" });
  await expect.poll(() => picker.hasAttribute("disabled")).toBe(false);
  await user.click(picker);
  await user.click(await screen.findByRole("option", { name }));
}

const field = () =>
  screen.getByRole<HTMLTextAreaElement>("textbox", { name: "span mapping" });

Element.prototype.scrollIntoView = () => {};

afterEach(closePage);

test("picking a preset fills the mapping with its JSON, which stays editable", async () => {
  const user = userEvent.setup();
  renderPicker();
  await pick(user, "gen_ai");
  expect(JSON.parse(field().value)).toEqual(presets[0].document);
  await user.type(field(), " ");
  expect(field().value.endsWith("\n ")).toBe(true);
  expect(
    screen.getByRole("link", { name: "Which preset fits your framework" }),
  ).toHaveAttribute("href", "/docs/framework-mappings");
});

test("switching presets without editing replaces the JSON quietly", async () => {
  const user = userEvent.setup();
  renderPicker();
  await pick(user, "gen_ai");
  await pick(user, "openinference");
  expect(JSON.parse(field().value)).toEqual(presets[1].document);
  expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
});

test("a pasted mapping is kept until a preset is picked, and Undo brings it back", async () => {
  const user = userEvent.setup();
  renderPicker();
  await user.click(field());
  await user.paste('{"schema_version": "1"}');
  expect(field().value).toBe('{"schema_version": "1"}');
  await pick(user, "openinference");
  expect(JSON.parse(field().value)).toEqual(presets[1].document);
  expect(
    screen.getByText(/The openinference preset replaced your edits/),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Undo" }));
  expect(field().value).toBe('{"schema_version": "1"}');
  expect(screen.queryByRole("button", { name: "Undo" })).toBeNull();
});

test("editing a picked preset by hand clears the preset name from the picker", async () => {
  const user = userEvent.setup();
  renderPicker();
  await pick(user, "gen_ai");
  const picker = screen.getByRole("button", { name: "mapping preset" });
  expect(picker).toHaveTextContent("gen_ai");
  await user.type(field(), " ");
  expect(picker).toHaveTextContent("Start from a preset…");
  await pick(user, "openinference");
  expect(
    screen.getByText(/The openinference preset replaced your edits/),
  ).toBeInTheDocument();
});

test("a pasted mapping stays restorable across several preset picks", async () => {
  const user = userEvent.setup();
  renderPicker();
  await user.click(field());
  await user.paste('{"schema_version": "1"}');
  await pick(user, "gen_ai");
  await pick(user, "openinference");
  expect(
    screen.getByText(/The openinference preset replaced your edits/),
  ).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "Undo" }));
  expect(field().value).toBe('{"schema_version": "1"}');
});
