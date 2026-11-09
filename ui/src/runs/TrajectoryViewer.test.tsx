// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
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
