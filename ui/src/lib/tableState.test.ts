import { describe, expect, it } from "vitest";
import { mergeOrder, moveColumn } from "./tableState";

describe("mergeOrder", () => {
  it("uses the default order when nothing is saved", () => {
    expect(mergeOrder([], ["a", "b", "c"])).toEqual(["a", "b", "c"]);
  });

  it("keeps the saved order and slots new columns after their default neighbour", () => {
    expect(mergeOrder(["c", "a"], ["a", "b", "c", "d"])).toEqual([
      "c",
      "d",
      "a",
      "b",
    ]);
  });

  it("puts a new first column first and drops removed columns", () => {
    expect(mergeOrder(["b", "gone"], ["new", "b"])).toEqual(["new", "b"]);
  });
});

describe("moveColumn", () => {
  it("moves a column forward to just after the target", () => {
    expect(moveColumn(["a", "b", "c", "d"], "a", "c")).toEqual([
      "b",
      "c",
      "a",
      "d",
    ]);
  });

  it("moves a column back to just before the target", () => {
    expect(moveColumn(["a", "b", "c", "d"], "d", "b")).toEqual([
      "a",
      "d",
      "b",
      "c",
    ]);
  });
});
