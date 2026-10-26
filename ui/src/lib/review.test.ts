import { describe, expect, it } from "vitest";
import {
  countRequested,
  isStale,
  scopeLabel,
  stateClass,
  stateLabel,
} from "./review";

describe("stateLabel / stateClass", () => {
  it("maps every state to copy and a chip class", () => {
    expect(stateLabel("draft")).toBe("Draft");
    expect(stateLabel("review_requested")).toBe("Review requested");
    expect(stateLabel("changes_requested")).toBe("Changes requested");
    expect(stateLabel("approved")).toBe("Approved");
    expect(stateClass("approved")).toBe("chip-pass");
    expect(stateClass("changes_requested")).toBe("chip-partial");
    expect(stateClass("review_requested")).toBe("chip-info");
    expect(stateClass("draft")).toBe("muted");
  });
});

describe("isStale", () => {
  it("is true only when the comment was written against other content", () => {
    expect(isStale({ content_hash: "a" }, "a")).toBe(false);
    expect(isStale({ content_hash: "a" }, "b")).toBe(true);
    expect(isStale({ content_hash: "a" }, undefined)).toBe(false);
  });
});

describe("scopeLabel", () => {
  it("describes approved-only versus drafts from experiment metadata", () => {
    expect(scopeLabel({ review_scope: "approved" }, 12, 40)).toBe(
      "12 of 40 cases · approved only",
    );
    expect(scopeLabel({ review_scope: "all" }, 40, 40)).toBe(
      "40 of 40 cases · includes drafts",
    );
    expect(scopeLabel({}, 3, 3)).toBeNull();
    expect(scopeLabel({ review_scope: null }, 3, 3)).toBeNull();
  });
});

describe("countRequested", () => {
  const examples = [
    { case_id: "a", split_tags: ["x"] },
    { case_id: "b", split_tags: ["y"] },
    { case_id: "c", split_tags: ["x", "y"] },
  ];
  it("counts the cases the requested selection names", () => {
    expect(countRequested(examples, { all: true })).toBe(3);
    expect(countRequested(examples, { tag: "x" })).toBe(2);
    expect(countRequested(examples, { case_ids: ["a", "b"] })).toBe(2);
    expect(countRequested(examples, undefined)).toBe(3);
  });
});
