import { useEffect, useState } from "react";
import { api, apiJson } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { isStale } from "../lib/review";
import type { CaseComment, CommentThread } from "../lib/types";
import { isReviewerOf } from "../lib/access";
import { splitRef } from "../lib/workspace";
import { useMe } from "../shell/capabilities";

export function CaseComments({
  dataset,
  caseId,
  currentHash,
  version,
}: {
  dataset: string;
  caseId: string;
  currentHash: string | undefined;
  version: number;
}) {
  const me = useMe();
  const base = `/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(caseId)}/comments`;
  const [threads, setThreads] = useState<CommentThread[] | null>(null);
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState<number | null>(null);
  const [showResolved, setShowResolved] = useState(false);

  useEffect(() => {
    api<{ threads: CommentThread[] }>(base).then((r) => setThreads(r.threads));
  }, [base, version]);

  const reload = () =>
    api<{ threads: CommentThread[] }>(base).then((r) => setThreads(r.threads));

  const post = async () => {
    if (!draft.trim()) return;
    await apiJson(base, "POST", { body: draft, parent_id: replyTo });
    setDraft("");
    setReplyTo(null);
    await reload();
  };

  const toggle = async (c: CaseComment) => {
    await apiJson(`${base}/${c.id}`, "PATCH", { resolved: !c.resolved });
    await reload();
  };

  const canResolve = (c: CaseComment) =>
    isReviewerOf(me, splitRef(dataset).workspace) || c.author === me.username;

  const Comment = ({ c, reply }: { c: CaseComment; reply?: boolean }) => (
    <div className={reply ? "ml-6 mt-2 border-l-2 border-line pl-3" : ""}>
      <div className="flex items-center gap-2 text-meta">
        <span className="font-medium">{c.author}</span>
        <span className="text-ink-3">{fmtWhen(c.created_at)}</span>
        {isStale(c, currentHash) ? (
          <span className="chip chip-partial">Case has been updated since</span>
        ) : null}
        {c.resolved ? <span className="chip muted">resolved</span> : null}
        {canResolve(c) ? (
          <button className="btn btn-sm ml-auto" onClick={() => toggle(c)}>
            {c.resolved ? "Unresolve" : "Resolve"}
          </button>
        ) : null}
        {!reply && !c.resolved ? (
          <button className="btn btn-sm" onClick={() => setReplyTo(c.id)}>
            Reply
          </button>
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
      <div className="card p-3">
        {threads === null ? <div className="pane-status">Loading…</div> : null}
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
          <button
            className="btn btn-sm mb-2"
            onClick={() => setShowResolved(!showResolved)}
          >
            {showResolved ? "Hide" : "Show"} {resolved.length} resolved thread
            {resolved.length === 1 ? "" : "s"}
          </button>
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
        <textarea
          className="input mt-2 w-full"
          rows={3}
          placeholder={
            replyTo === null ? "Write a comment…" : `Reply to #${replyTo}…`
          }
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
        />
        <div className="toolbar mt-2 justify-end">
          {replyTo !== null ? (
            <button className="btn btn-sm" onClick={() => setReplyTo(null)}>
              Cancel reply
            </button>
          ) : null}
          <button
            className="btn btn-sm"
            onClick={post}
            disabled={!draft.trim()}
          >
            {replyTo === null ? "Comment" : "Reply"}
          </button>
        </div>
      </div>
    </>
  );
}
