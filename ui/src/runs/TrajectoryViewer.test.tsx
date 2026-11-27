// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import fixture from "../lib/atif_full.fixture.json";
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
  Element.prototype.scrollIntoView = () => {};
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

  await user.click(
    screen.getByRole("button", { name: /Where the subagent starts/ }),
  );

  const shown = document.querySelector(`[data-step-id="${step.step_id}"]`)!;
  expect(shown.className).toContain("bg-info-bg");
  expect(
    screen
      .getAllByRole("tab")
      .find((tab) => tab.getAttribute("aria-selected") === "true")!.textContent,
  ).toContain("subagent");
});

test("jumping to a step the filters hide turns the filters back on", async () => {
  const user = userEvent.setup();
  Element.prototype.scrollIntoView = () => {};
  const steps = doc.steps.map((step) =>
    step.step_id === 2 ? { ...step, message: "" } : step,
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
  expect(document.querySelector('[data-step-id="2"]')!.className).toContain(
    "bg-info-bg",
  );
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
