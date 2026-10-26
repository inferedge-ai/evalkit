import { describe, expect, it } from "vitest";
import { fixtureEntries, fixturePath } from "./fixtures";

describe("fixtureEntries", () => {
  it("maps case files to names and sha download paths", () => {
    const sha = "a".repeat(64);
    expect(
      fixtureEntries([{ key: "bs fy25.csv", sha256: sha, context: "BS" }]),
    ).toEqual([
      {
        key: "bs fy25.csv",
        name: "bs fy25.csv",
        context: "BS",
        path: `/fixtures/sha256/${sha}?name=bs%20fy25.csv`,
      },
    ]);
    expect(fixturePath(sha, "x.csv")).toBe(
      `/fixtures/sha256/${sha}?name=x.csv`,
    );
  });

  it("returns nothing for cases without files", () => {
    expect(fixtureEntries([])).toEqual([]);
    expect(fixtureEntries(null)).toEqual([]);
  });
});
