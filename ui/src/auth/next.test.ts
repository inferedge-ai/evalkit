import { describe, expect, it } from "vitest";
import { safeNext } from "./next";

describe("safeNext", () => {
  it("keeps in-app paths with query and hash", () => {
    expect(safeNext("/datasets/x?tab=1#a")).toBe("/datasets/x?tab=1#a");
  });

  it.each([
    null,
    "",
    "datasets",
    "https://evil.example",
    "//evil.example",
    "/\\evil.example",
    "/login",
    "/login?next=/",
  ])("falls back to / for %s", (raw) => {
    expect(safeNext(raw)).toBe("/");
  });
});
