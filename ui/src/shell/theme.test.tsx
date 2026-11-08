// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { ThemeProvider, useTheme } from "./theme";

function ThemeButton() {
  const { theme, toggle } = useTheme();
  return <button onClick={toggle}>{theme}</button>;
}

test("switching to dark mode puts the dark class on the page and keeps it", async () => {
  localStorage.setItem("devtools.theme", "light");
  render(
    <ThemeProvider>
      <ThemeButton />
    </ThemeProvider>,
  );
  expect(document.documentElement).not.toHaveClass("dark");

  await userEvent.click(screen.getByRole("button", { name: "light" }));

  expect(document.documentElement).toHaveClass("dark");
  expect(localStorage.getItem("devtools.theme")).toBe("dark");
});
