import { describe, expect, it } from "vitest";
import { scoreValue } from "./bucketColumns";

describe("scoreValue", () => {
  it("returns null for missing, non-numeric, or non-finite values", () => {
    expect(scoreValue(undefined, "x")).toBeNull();
    expect(scoreValue(null, "x")).toBeNull();
    expect(scoreValue({ x: null }, "x")).toBeNull();
    expect(scoreValue({ x: NaN }, "x")).toBeNull();
    expect(scoreValue({ x: 0 }, "x")).toBe(0);
  });
});
