import { describe, expect, it } from "vitest";
import { isStale, stateClass, stateLabel } from "./review";

describe("stateLabel / stateClass", () => {
  it("maps every state to copy and a chip class", () => {
    expect(stateLabel("unreviewed")).toBe("Unreviewed");
    expect(stateLabel("review_requested")).toBe("Review requested");
    expect(stateLabel("changes_requested")).toBe("Changes requested");
    expect(stateLabel("approved")).toBe("Approved");
    expect(stateClass("approved")).toBe("chip-pass");
    expect(stateClass("changes_requested")).toBe("chip-partial");
    expect(stateClass("review_requested")).toBe("chip-info");
    expect(stateClass("unreviewed")).toBe("muted");
  });
});

describe("isStale", () => {
  it("is true only when the comment was written against other content", () => {
    expect(isStale({ content_hash: "a" }, "a")).toBe(false);
    expect(isStale({ content_hash: "a" }, "b")).toBe(true);
    expect(isStale({ content_hash: "a" }, undefined)).toBe(false);
  });
});
