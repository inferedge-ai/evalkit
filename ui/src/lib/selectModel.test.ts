import { describe, expect, it } from "vitest";
import { filterOptions, nextIndex, typeaheadIndex } from "./selectModel";

const options = [
  { value: "a", label: "alpha" },
  { value: "b", label: "beta", disabled: true },
  { value: "c", label: "gamma" },
  { value: "d", label: "Delta" },
];

describe("filterOptions", () => {
  it("keeps every option for a blank filter", () => {
    expect(filterOptions(options, "  ")).toHaveLength(4);
  });

  it("matches labels case-insensitively", () => {
    expect(filterOptions(options, "EL").map((o) => o.value)).toEqual(["d"]);
  });
});

describe("nextIndex", () => {
  it("skips disabled options", () => {
    expect(nextIndex(options, 0, 1)).toBe(2);
    expect(nextIndex(options, 2, -1)).toBe(0);
  });

  it("stays put at either end", () => {
    expect(nextIndex(options, 3, 1)).toBe(3);
    expect(nextIndex(options, 0, -1)).toBe(0);
  });
});

describe("typeaheadIndex", () => {
  it("finds the next enabled label starting with the key, wrapping", () => {
    expect(typeaheadIndex(options, 0, "d")).toBe(3);
    expect(typeaheadIndex(options, 3, "a")).toBe(0);
    expect(typeaheadIndex(options, 0, "b")).toBe(-1);
  });
});
