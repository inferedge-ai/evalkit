// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import fixture from "../lib/atif_full.fixture.json";
import type { RunSidebarItem } from "../lib/extensions";
import type { AtifTrajectory } from "../lib/types";
import { TrajectoryViewer } from "./TrajectoryViewer";

const doc = fixture as unknown as AtifTrajectory;
const thinking = "Need the 10-K revenue and net income figures.";
const thinkingBlock = () =>
  screen
    .queryAllByText("thinking")
    .find((el) => el.closest(".collapsible-header"));

test("the thinking and tool calls filters hide and show those parts of the trajectory", async () => {
  const user = userEvent.setup();
  render(<TrajectoryViewer doc={doc} />);
  await user.click(thinkingBlock()!);
  expect(screen.getByText(thinking)).toBeTruthy();
  expect(screen.getAllByText("web_search").length).toBeGreaterThan(0);

  await user.click(screen.getByRole("checkbox", { name: "thinking" }));
  expect(screen.queryByText(thinking)).toBeNull();
  expect(thinkingBlock()).toBeUndefined();

  await user.click(screen.getByText("tool calls"));
  expect(screen.queryAllByText("web_search")).toHaveLength(0);

  await user.click(screen.getByRole("checkbox", { name: "thinking" }));
  expect(thinkingBlock()).toBeDefined();
});

test("clicking the open tab closes the tool calls opened in it", async () => {
  const user = userEvent.setup();
  render(<TrajectoryViewer doc={doc} />);
  const searchCall = screen
    .getAllByText("web_search")
    .find((el) => el.closest(".collapsible-header"))!;
  const isOpen = () =>
    searchCall.closest(".collapsible")!.classList.contains("is-open");
  await user.click(searchCall);
  expect(isOpen()).toBe(true);

  await user.click(screen.getByRole("tab", { name: "myagent (3)" }));
  expect(isOpen()).toBe(false);
});

test("an extra sidebar section lists its items above Prompts and jumps to a step of another tab", async () => {
  const user = userEvent.setup();
  const scrolledTo: (string | null)[] = [];
  Element.prototype.scrollIntoView = function (this: Element) {
    scrolledTo.push(this.textContent);
  };
  const subagent = doc.subagent_trajectories![0];
  const step = subagent.steps[0];
  render(
    <MemoryRouter>
      <TrajectoryViewer
        doc={doc}
        sections={[
          {
            title: "Notes",
            items: [
              {
                key: "a",
                label: "Where the subagent starts",
                tab: subagent.trajectory_id!,
                stepId: step.step_id,
              },
              { key: "b", label: "About the whole run" },
              { key: "c", label: "Open the note", to: "/notes/7" },
            ],
          },
        ]}
      />
    </MemoryRouter>,
  );
  expect(screen.getByRole("link", { name: "Open the note" })).toHaveAttribute(
    "href",
    "/notes/7",
  );
  const titles = screen
    .getAllByText(/^(Notes|Prompts)$/)
    .map((el) => el.textContent);
  expect(titles).toEqual(["Notes", "Prompts"]);
  expect(screen.getByText("About the whole run").closest("button")).toBeNull();

  await user.click(screen.getByRole("checkbox", { name: "thinking" }));
  await user.click(
    screen.getByRole("button", { name: /Where the subagent starts/ }),
  );

  const shown = document.querySelector(`[data-step-id="${step.step_id}"]`)!;
  expect(shown.className).toContain("bg-info-bg");
  expect(shown.textContent).toContain(step.message as string);
  expect(scrolledTo).toEqual([shown.textContent]);
  expect(screen.getByRole("checkbox", { name: "thinking" })).not.toBeChecked();
  expect(
    screen
      .getAllByRole("tab")
      .find((tab) => tab.getAttribute("aria-selected") === "true")!.textContent,
  ).toContain("subagent");
});

test("jumping to a step the filters hide turns on only the filter that hides it", async () => {
  const user = userEvent.setup();
  const scrollIntoView = vi.fn();
  Element.prototype.scrollIntoView = scrollIntoView;
  const steps = doc.steps.map((step) =>
    step.step_id === 2
      ? { ...step, message: "", reasoning_content: null }
      : step,
  );
  render(
    <MemoryRouter>
      <TrajectoryViewer
        doc={{ ...doc, steps }}
        sections={[
          {
            title: "Notes",
            items: [{ key: "a", label: "The search", stepId: 2 }],
          },
        ]}
      />
    </MemoryRouter>,
  );
  await user.click(screen.getByRole("checkbox", { name: "thinking" }));
  await user.click(screen.getByRole("checkbox", { name: "tool calls" }));
  expect(document.querySelector('[data-step-id="2"]')).toBeNull();

  await user.click(screen.getByRole("button", { name: /The search/ }));

  expect(screen.getByRole("checkbox", { name: "tool calls" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "thinking" })).not.toBeChecked();
  const shown = document.querySelector('[data-step-id="2"]')!;
  expect(shown.className).toContain("bg-info-bg");
  expect(scrollIntoView).toHaveBeenCalledTimes(1);
  expect(scrollIntoView.mock.contexts[0]).toBe(shown);
});

test("an item is a step, a link or plain text, never a step and a link", () => {
  const evidence: { tab: string | null; step_id: number | null } = {
    tab: "main",
    step_id: 1,
  };
  const items: RunSidebarItem[] = [
    { key: "a", label: "A step", stepId: 1 },
    { key: "b", label: "A step of a subagent", tab: "sub", stepId: 1 },
    { key: "c", label: "A link", to: "/notes/7" },
    { key: "d", label: "Plain text" },
    {
      key: "e",
      label: "A step when there is one",
      ...(evidence.tab !== null && evidence.step_id !== null
        ? { tab: evidence.tab, stepId: evidence.step_id }
        : {}),
    },
    // @ts-expect-error an item cannot both jump to a step and link to a page
    { key: "f", label: "Both", stepId: 1, to: "/notes/7" },
  ];
  expect(items).toHaveLength(6);
});

test("an item whose step or trajectory the run does not have is plain text", () => {
  render(
    <MemoryRouter>
      <TrajectoryViewer
        doc={doc}
        sections={[
          {
            title: "Notes",
            items: [
              { key: "a", label: "A step that is not there", stepId: 99 },
              {
                key: "b",
                label: "A trajectory that is not there",
                tab: "nope",
                stepId: 1,
              },
            ],
          },
          { title: "Notes", items: [{ key: "a", label: "Same title again" }] },
        ]}
      />
    </MemoryRouter>,
  );

  expect(
    screen.getByText("A step that is not there").closest("button"),
  ).toBeNull();
  expect(
    screen.getByText("A trajectory that is not there").closest("button"),
  ).toBeNull();
  expect(screen.getByText("Same title again")).toBeTruthy();
});
