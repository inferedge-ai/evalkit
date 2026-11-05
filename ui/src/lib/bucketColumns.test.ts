import { describe, expect, it } from "vitest";
import { scoredBucketColumns } from "./bucketColumns";

describe("scoredBucketColumns", () => {
  it("keeps only buckets some row scored, custom included", () => {
    const columns = scoredBucketColumns([
      { scores: { presentation: 0.5, custom: null } },
      { scores: { custom: 0.8, trajectory: null } },
    ]);
    expect(columns.map((c) => c.bucket)).toEqual(["presentation", "custom"]);
  });

  it("returns no columns when nothing is scored", () => {
    expect(scoredBucketColumns([])).toEqual([]);
  });
});
