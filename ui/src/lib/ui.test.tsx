// @vitest-environment jsdom
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import { describeTerm } from "./glossary";
import { InfoTip } from "./ui";

const TERM = "answer_rubric_coverage";
const DESCRIPTION = describeTerm(TERM)!;

test("the ⓘ shows the term's description while the pointer rests on it", async () => {
  const user = userEvent.setup();
  render(
    <p>
      coverage <InfoTip term={TERM} />
    </p>,
  );
  expect(screen.queryByText(DESCRIPTION)).not.toBeInTheDocument();

  await user.hover(screen.getByLabelText(DESCRIPTION));
  expect(await screen.findByText(DESCRIPTION)).toBeVisible();

  await user.unhover(screen.getByLabelText(DESCRIPTION));
  await waitFor(() =>
    expect(screen.queryByText(DESCRIPTION)).not.toBeInTheDocument(),
  );
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
  expect(screen.queryByText(DESCRIPTION)).not.toBeInTheDocument();

  await user.tab();
  expect(screen.getByLabelText(DESCRIPTION)).toHaveFocus();
  expect(await screen.findByText(DESCRIPTION)).toBeVisible();

  await user.tab();
  await waitFor(() =>
    expect(screen.queryByText(DESCRIPTION)).not.toBeInTheDocument(),
  );
});
