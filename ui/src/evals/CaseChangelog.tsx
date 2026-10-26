import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { fmtWhen } from "../lib/format";
import type { CaseEvent } from "../lib/types";

const KIND_LABEL: Record<CaseEvent["kind"], string> = {
  synced: "synced",
  review_requested: "review requested",
  approved: "approved",
  changes_requested: "changes requested",
};

const SHOWN_BY_DEFAULT = 4;

export function CaseChangelog({
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
  const [events, setEvents] = useState<CaseEvent[] | null>(null);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    api<{ events: CaseEvent[] }>(
      `/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(caseId)}/events`,
    ).then((r) => setEvents(r.events));
  }, [dataset, caseId, version]);

  const all = events ?? [];
  const shown = expanded ? all : all.slice(0, SHOWN_BY_DEFAULT);
  const hidden = all.length - shown.length;

  return (
    <>
      <div className="eyebrow">Changelog</div>
      <div className="card">
        <div className="rows">
          {shown.map((e) => {
            const previous =
              currentHash !== undefined && e.content_hash !== currentHash;
            return (
              <div
                key={e.id}
                className={`flex flex-col gap-0.5 px-3 py-2 ${previous ? "text-ink-4" : ""}`}
              >
                <div className="flex min-w-0 items-baseline gap-2">
                  <span className="shrink-0 font-medium">
                    {KIND_LABEL[e.kind]}
                  </span>
                  <span className="min-w-0 truncate text-ink-3">
                    {e.actor}
                    {e.target ? ` → ${e.target}` : ""}
                  </span>
                </div>
                <div className="flex min-w-0 items-baseline gap-2 text-micro text-ink-3">
                  <span className="shrink-0">{fmtWhen(e.created_at)}</span>
                  <span
                    className="min-w-0 truncate font-mono"
                    title={
                      previous
                        ? "Content hash of the case when this happened. The case has been edited since, so this event no longer counts toward its state."
                        : "Content hash of the case when this happened. Matches the current content."
                    }
                  >
                    {e.content_hash}
                    {previous ? " · previous content" : ""}
                  </span>
                </div>
              </div>
            );
          })}
          {events && events.length === 0 ? (
            <div className="pane-status">No events yet.</div>
          ) : null}
          {hidden > 0 || expanded ? (
            <button
              className="w-full px-3 py-1.5 text-left text-micro text-brand hover:underline"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? "Show fewer" : `Show ${hidden} more`}
            </button>
          ) : null}
        </div>
      </div>
    </>
  );
}
