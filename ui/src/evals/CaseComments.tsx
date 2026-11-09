import { useState } from "react";
import { apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { refreshQueries, useApi } from "../lib/query";
import { isStale } from "../lib/review";
import type { CaseComment, CommentThread } from "../lib/types";
import { isReviewerOf } from "../lib/access";
import { splitRef } from "../lib/workspace";
import { useMe } from "../shell/capabilities";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

export function CaseComments({
  dataset,
  caseId,
  currentHash,
}: {
  dataset: string;
  caseId: string;
  currentHash: string | undefined;
}) {
  const me = useMe();
  const base = `/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(caseId)}/comments`;
  const threads =
    useApi<{ threads: CommentThread[] }>(base).data?.threads ?? null;
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState<number | null>(null);
  const [showResolved, setShowResolved] = useState(false);

  const post = async () => {
    if (!draft.trim()) return;
    await apiJson(base, "POST", { body: draft, parent_id: replyTo });
    setDraft("");
    setReplyTo(null);
    await refreshQueries();
  };

  const toggle = async (c: CaseComment) => {
    await apiJson(`${base}/${c.id}`, "PATCH", { resolved: !c.resolved });
    await refreshQueries();
  };

  const canResolve = (c: CaseComment) =>
    isReviewerOf(me, splitRef(dataset).workspace) || c.author === me.username;

  const Comment = ({ c, reply }: { c: CaseComment; reply?: boolean }) => (
    <div className={reply ? "ml-6 mt-2 border-l-2 border-line pl-3" : ""}>
      <div className="flex items-center gap-2 text-meta">
        <span className="font-medium">{c.author}</span>
        <span className="text-ink-3">{fmtWhen(c.created_at)}</span>
        {isStale(c, currentHash) ? (
          <Badge variant="partial">Case has been updated since</Badge>
        ) : null}
        {c.resolved ? <Badge variant="secondary">resolved</Badge> : null}
        {canResolve(c) ? (
          <Button
            variant="outline"
            size="sm"
            className="ml-auto"
            onClick={() => toggle(c)}
          >
            {c.resolved ? "Unresolve" : "Resolve"}
          </Button>
        ) : null}
        {!reply && !c.resolved ? (
          <Button variant="outline" size="sm" onClick={() => setReplyTo(c.id)}>
            Reply
          </Button>
        ) : null}
      </div>
      {c.resolved && reply ? null : (
        <div className="mt-1 whitespace-pre-wrap">{c.body}</div>
      )}
    </div>
  );

  const open = (threads ?? []).filter((t) => !t.resolved);
  const resolved = (threads ?? []).filter((t) => t.resolved);

  return (
    <>
      <div className="eyebrow">Comments</div>
      <Card size="sm">
        <CardContent>
          {threads === null ? (
            <div className="pane-status">Loading…</div>
          ) : null}
          {open.map((t) => (
            <div
              key={t.id}
              className="mb-3 border-b border-line pb-3 last:border-0"
            >
              <Comment c={t} />
              {t.replies.map((r) => (
                <Comment key={r.id} c={r} reply />
              ))}
            </div>
          ))}
          {resolved.length > 0 ? (
            <Button
              variant="outline"
              size="sm"
              className="mb-2"
              onClick={() => setShowResolved(!showResolved)}
            >
              {showResolved ? "Hide" : "Show"} {resolved.length} resolved thread
              {resolved.length === 1 ? "" : "s"}
            </Button>
          ) : null}
          {showResolved
            ? resolved.map((t) => (
                <div
                  key={t.id}
                  className="mb-3 border-b border-line pb-3 opacity-70 last:border-0"
                >
                  <Comment c={t} />
                  {t.replies.map((r) => (
                    <Comment key={r.id} c={r} reply />
                  ))}
                </div>
              ))
            : null}
          <Textarea
            className="mt-2 w-full"
            rows={3}
            placeholder={
              replyTo === null ? "Write a comment…" : `Reply to #${replyTo}…`
            }
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="toolbar mt-2 justify-end">
            {replyTo !== null ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setReplyTo(null)}
              >
                Cancel reply
              </Button>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={post}
              disabled={!draft.trim()}
            >
              {replyTo === null ? "Comment" : "Reply"}
            </Button>
          </div>
        </CardContent>
      </Card>
    </>
  );
}
