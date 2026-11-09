import { useState } from "react";
import { Check } from "lucide-react";
import { Link } from "react-router-dom";
import { apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { stateClass, stateLabel } from "../lib/review";
import type { ReviewSummary } from "../lib/types";
import { isReviewerOf } from "../lib/access";
import { splitRef } from "../lib/workspace";
import { useMe } from "../shell/capabilities";
import { ToArrow } from "../lib/ui";
import { Button } from "@/components/ui/button";

type Action = "approve" | "request_changes";

export function ReviewPanel({
  dataset,
  caseId,
  review,
  twin,
  onChanged,
}: {
  dataset: string;
  caseId: string;
  review: ReviewSummary;
  twin?: { href: string; text: string; locked: boolean } | null;
  onChanged: () => void;
}) {
  const me = useMe();
  const [action, setAction] = useState<Action | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!action) return;
    setBusy(true);
    setError(null);
    try {
      await apiJson(
        `/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(caseId)}/review`,
        "POST",
        { action, comment: comment.trim() || null },
      );
      setAction(null);
      setComment("");
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const needsComment = action === "request_changes" && !comment.trim();
  const actionVariant = action === "approve" ? "default" : "destructive";

  return (
    <>
      <div className="eyebrow">Review</div>
      <div className="card p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`chip ${stateClass(review.state)}`}>
            {stateLabel(review.state)}
          </span>
          {review.state === "review_requested" && review.requested_to ? (
            <span className="row-meta">
              <ToArrow /> {review.requested_to}
            </span>
          ) : null}
          {review.updated_at ? (
            <span className="row-meta">{fmtWhen(review.updated_at)}</span>
          ) : null}
        </div>
        {twin ? (
          <div className="mt-3 text-meta text-ink-3 whitespace-normal break-words">
            {twin.locked ? "This dataset is fed by promotion. " : null}
            <Link className="text-brand hover:underline" to={twin.href}>
              {twin.text}
            </Link>
          </div>
        ) : null}
        {!twin?.locked &&
        isReviewerOf(me, splitRef(dataset).workspace) &&
        action === null ? (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="lg" onClick={() => setAction("approve")}>
              Approve
            </Button>
            <Button
              variant="destructive"
              size="lg"
              onClick={() => setAction("request_changes")}
            >
              Request changes
            </Button>
          </div>
        ) : null}
        {action ? (
          <div className="mt-3">
            <textarea
              className="input w-full"
              rows={3}
              placeholder={
                action === "approve"
                  ? "Comment (optional)"
                  : "What needs to change?"
              }
              value={comment}
              onChange={(e) => setComment(e.target.value)}
            />
            {error ? <div className="pane-status error">{error}</div> : null}
            <div className="mt-2 flex flex-wrap justify-end gap-2">
              <Button
                variant="outline"
                onClick={() => setAction(null)}
                disabled={busy}
              >
                Cancel
              </Button>
              <Button
                size="lg"
                variant={actionVariant}
                onClick={submit}
                disabled={busy || needsComment}
              >
                {action === "approve" ? (
                  <>
                    Approve <Check size={14} />
                  </>
                ) : (
                  "Request changes"
                )}
              </Button>
            </div>
          </div>
        ) : null}
      </div>
    </>
  );
}
