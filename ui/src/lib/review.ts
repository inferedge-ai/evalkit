import type { ReviewState } from "./types";

const LABEL: Record<ReviewState, string> = {
  draft: "Draft",
  review_requested: "Review requested",
  changes_requested: "Changes requested",
  approved: "Approved",
};

const CLASS: Record<ReviewState, string> = {
  draft: "muted",
  review_requested: "chip-info",
  changes_requested: "chip-partial",
  approved: "chip-pass",
};

export const REVIEW_STATES: ReviewState[] = [
  "draft",
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

export function scopeLabel(
  meta: { review_scope?: unknown },
  ran: number,
  total: number,
): string | null {
  if (meta.review_scope === "approved")
    return `${ran} of ${total} cases · approved only`;
  if (meta.review_scope === "all")
    return `${ran} of ${total} cases · includes drafts`;
  return null;
}

export function countRequested(
  examples: { case_id: string; split_tags: string[] }[],
  requested: unknown,
): number {
  const sel = (requested ?? {}) as {
    all?: boolean;
    tag?: string;
    case_ids?: string[];
  };
  if (sel.tag) {
    const tag = sel.tag;
    return examples.filter((e) => e.split_tags.includes(tag)).length;
  }
  if (sel.case_ids) return sel.case_ids.length;
  return examples.length;
}
