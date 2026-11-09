// @vitest-environment jsdom
import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { ScoreDetail } from "./bits";

test("a rubric table names each item's verdict for a screen reader", () => {
  render(
    <ScoreDetail
      score={{
        score: 0.5,
        label: null,
        explanation: null,
        items: [
          { rubric: "states the late fee", verdict: "satisfied" },
          { rubric: "cites the policy", verdict: "missing" },
        ],
      }}
    />,
  );

  const verdictOf = (rubric: RegExp) =>
    within(screen.getByRole("row", { name: rubric })).getByRole("img");
  expect(verdictOf(/late fee/)).toHaveAccessibleName("satisfied");
  expect(verdictOf(/policy/)).toHaveAccessibleName("absent");
});
