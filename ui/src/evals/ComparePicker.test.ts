import { describe, expect, it } from "vitest";
import { compareOptions, compareUrl } from "./ComparePicker";
import type { ExperimentListItem } from "../lib/types";

const exp = (id: number, number: number, model: string | null) =>
  ({
    id,
    number,
    agent: "research-agent@dev",
    config: { model },
  }) as unknown as ExperimentListItem;

describe("ComparePicker", () => {
  it("builds the compare URL with the picked experiment as base", () => {
    expect(compareUrl(3, 7)).toBe("/experiments/compare?base=3&compare=7");
  });

  it("offers the dataset's other experiments", () => {
    const options = compareOptions(
      [exp(7, 3, null), exp(5, 2, "vendor/big-1"), exp(2, 1, null)],
      7,
    );
    expect(options).toEqual([
      { value: 5, label: "#2 · research-agent@dev · big-1" },
      { value: 2, label: "#1 · research-agent@dev" },
    ]);
  });
});
