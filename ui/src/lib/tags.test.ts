import { describe, expect, it } from "vitest";
import { hasAnyTag, sortedTags, tagCounts } from "./tags";
import type { CaseView } from "./types";

const ex = (case_id: string, tags: string[]) =>
  ({ case_id, split_tags: tags }) as unknown as CaseView;

describe("hasAnyTag", () => {
  it("matches a case carrying any of the wanted tags", () => {
    expect(hasAnyTag(["ratios", "tcs"], ["mf", "tcs"])).toBe(true);
    expect(hasAnyTag(["ratios"], ["mf"])).toBe(false);
    expect(hasAnyTag([], ["mf"])).toBe(false);
  });
});

describe("sortedTags", () => {
  it("sorts tags by name without changing the input", () => {
    const picked = ["statements", "mf", "ratios"];
    expect(sortedTags(picked)).toEqual(["mf", "ratios", "statements"]);
    expect(picked).toEqual(["statements", "mf", "ratios"]);
  });
});

describe("tagCounts", () => {
  it("counts cases per tag, sorted by name", () => {
    expect(
      tagCounts([ex("a", ["tcs", "ratios"]), ex("b", ["ratios"]), ex("c", [])]),
    ).toEqual([
      { name: "ratios", count: 2 },
      { name: "tcs", count: 1 },
    ]);
  });
});
