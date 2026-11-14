import { describe, expect, it } from "vitest";
import { getStateVariant, isStale, stateLabel } from "./review";

describe("stateLabel / getStateVariant", () => {
  it("maps every state to a label and a badge variant", () => {
    expect(stateLabel("unreviewed")).toBe("Unreviewed");
    expect(stateLabel("review_requested")).toBe("Review requested");
    expect(stateLabel("changes_requested")).toBe("Changes requested");
    expect(stateLabel("approved")).toBe("Approved");
    expect(stateLabel("rejected")).toBe("Rejected");
    expect(getStateVariant("approved")).toBe("pass");
    expect(getStateVariant("changes_requested")).toBe("partial");
    expect(getStateVariant("review_requested")).toBe("info");
    expect(getStateVariant("unreviewed")).toBe("secondary");
    expect(getStateVariant("rejected")).toBe("fail");
  });
});

describe("isStale", () => {
  it("is true only when the comment was written against other content", () => {
    expect(isStale({ content_hash: "a" }, "a")).toBe(false);
    expect(isStale({ content_hash: "a" }, "b")).toBe(true);
    expect(isStale({ content_hash: "a" }, undefined)).toBe(false);
  });
});
