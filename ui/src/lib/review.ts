import type { BadgeVariant } from "@/components/ui/badge";
import type { ReviewState } from "./types";

const LABEL: Record<ReviewState, string> = {
  unreviewed: "Unreviewed",
  review_requested: "Review requested",
  changes_requested: "Changes requested",
  approved: "Approved",
  rejected: "Rejected",
};

const VARIANT: Record<ReviewState, BadgeVariant> = {
  unreviewed: "secondary",
  review_requested: "info",
  changes_requested: "partial",
  approved: "pass",
  rejected: "fail",
};

export const REVIEW_STATES: ReviewState[] = [
  "unreviewed",
  "review_requested",
  "changes_requested",
  "approved",
  "rejected",
];

export function stateLabel(state: ReviewState): string {
  return LABEL[state];
}

export function getStateVariant(state: ReviewState): BadgeVariant {
  return VARIANT[state];
}

export function isStale(
  comment: { content_hash: string },
  currentHash: string | undefined,
): boolean {
  return currentHash !== undefined && comment.content_hash !== currentHash;
}
