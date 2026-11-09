// @vitest-environment jsdom
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { describeTerm } from "./glossary";
import { InfoTip } from "./ui";

const TERM = "answer_rubric_coverage";
const DESCRIPTION = describeTerm(TERM)!;

// The tooltip popup, drawn in a portal while it is open.
function shownTooltip(): HTMLElement | null {
  return document.querySelector('[data-slot="tooltip-content"]');
}

test("the ⓘ shows the term's description while the pointer rests on it, a click included", async () => {
  const user = userEvent.setup();
  render(
    <p>
      coverage <InfoTip term={TERM} />
    </p>,
  );
  expect(shownTooltip()).toBeNull();

  await user.hover(screen.getByRole("button", { name: "definition" }));
  await waitFor(() => expect(shownTooltip()).toHaveTextContent(DESCRIPTION));
  await user.click(screen.getByRole("button", { name: "definition" }));
  expect(shownTooltip()).toHaveTextContent(DESCRIPTION);

  await user.unhover(screen.getByRole("button", { name: "definition" }));
  await waitFor(() => expect(shownTooltip()).toBeNull());
});

test("the ⓘ shows the term's description when a keyboard user tabs to it", async () => {
  const user = userEvent.setup();
  render(
    <p>
      <button>before</button> coverage <InfoTip term={TERM} />
      <button>after</button>
    </p>,
  );
  await user.tab();
  expect(shownTooltip()).toBeNull();

  await user.tab();
  expect(screen.getByRole("button", { name: "definition" })).toHaveFocus();
  await waitFor(() => expect(shownTooltip()).toHaveTextContent(DESCRIPTION));

  await user.tab();
  await waitFor(() => expect(shownTooltip()).toBeNull());
});

test("a tap on the ⓘ shows the description, and another tap hides it", async () => {
  render(
    <p>
      coverage <InfoTip term={TERM} />
    </p>,
  );
  const tip = screen.getByRole("button", { name: "definition" });
  expect(tip).toHaveAccessibleDescription(DESCRIPTION);

  fireEvent.pointerDown(tip, { pointerType: "touch" });
  fireEvent.click(tip);
  await waitFor(() => expect(shownTooltip()).toHaveTextContent(DESCRIPTION));

  fireEvent.pointerDown(tip, { pointerType: "touch" });
  fireEvent.click(tip);
  await waitFor(() => expect(shownTooltip()).toBeNull());
});

test("a score named like an object property has no ⓘ", () => {
  render(
    <p>
      constructor <InfoTip term="constructor" /> __proto__{" "}
      <InfoTip term="__proto__" />
    </p>,
  );

  expect(screen.queryByRole("button")).not.toBeInTheDocument();
});
