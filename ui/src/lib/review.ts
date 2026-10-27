import type { ReviewState } from "./types";

const LABEL: Record<ReviewState, string> = {
  unreviewed: "Unreviewed",
  review_requested: "Review requested",
  changes_requested: "Changes requested",
  approved: "Approved",
};

const CLASS: Record<ReviewState, string> = {
  unreviewed: "muted",
  review_requested: "chip-info",
  changes_requested: "chip-partial",
  approved: "chip-pass",
};

export const REVIEW_STATES: ReviewState[] = [
  "unreviewed",
  "review_requested",
  "changes_requested",
  "approved",
];

export function stateLabel(state: ReviewState): string {
  return LABEL[state];
}

export function stateClass(state: ReviewState): string {
  return CLASS[state];
}

export function isStale(
  comment: { content_hash: string },
  currentHash: string | undefined,
): boolean {
  return currentHash !== undefined && comment.content_hash !== currentHash;
}
