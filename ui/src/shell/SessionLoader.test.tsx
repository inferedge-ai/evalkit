// @vitest-environment jsdom
import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import { closePage } from "@/testFixtures";
import { LOADER_LINES, SessionError, SessionLoader } from "./SessionLoader";

afterEach(() => {
  closePage();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const shownLine = () =>
  LOADER_LINES.find((l) => screen.queryByText(l) !== null);

test("the session loader shows the logo and one line, then a different line", () => {
  vi.useFakeTimers();
  vi.spyOn(Math, "random").mockReturnValue(0);
  render(<SessionLoader />);

  expect(
    screen.getByRole("status", { name: "Checking session…" }),
  ).toBeVisible();
  expect(screen.getByRole("img", { name: "evalkit" })).toBeInTheDocument();
  expect(shownLine()).toBe(LOADER_LINES[0]);

  act(() => vi.advanceTimersByTime(2400));
  expect(shownLine()).toBe(LOADER_LINES[1]);
});

test("the session error shows what failed and reloads the page on Retry", () => {
  const reload = vi.fn();
  vi.stubGlobal("location", { ...window.location, reload });
  render(<SessionError detail="Error: 502 Bad Gateway" />);

  const alert = screen.getByRole("alert");
  expect(alert).toHaveTextContent("evalkit could not check your sign-in.");
  expect(alert).toHaveTextContent("Error: 502 Bad Gateway");

  screen.getByRole("button", { name: "Retry" }).click();
  expect(reload).toHaveBeenCalledOnce();
  vi.unstubAllGlobals();
});
