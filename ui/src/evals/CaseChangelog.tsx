import { useState } from "react";
import { History } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Empty } from "@/components/widgets/Empty";
import { fmtWhen } from "../lib/format";
import { useApi } from "../lib/query";
import { ToArrow } from "../lib/ui";
import type { CaseEvent } from "../lib/types";
import { Card } from "@/components/ui/card";

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
}: {
  dataset: string;
  caseId: string;
  currentHash: string | undefined;
}) {
  const events =
    useApi<{ events: CaseEvent[] }>(
      `/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(caseId)}/events`,
    ).data?.events ?? null;
  const [expanded, setExpanded] = useState(false);

  const all = events ?? [];
  const shown = expanded ? all : all.slice(0, SHOWN_BY_DEFAULT);
  const hidden = all.length - shown.length;

  return (
    <>
      <div className="eyebrow">Changelog</div>
      <Card className="gap-0 py-0">
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
                    {e.target ? (
                      <>
                        {" "}
                        <ToArrow /> {e.target}
                      </>
                    ) : null}
                    {e.dataset !== dataset ? ` · in ${e.dataset}` : ""}
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
          {events === null
            ? Array.from({ length: 2 }, (_, i) => (
                <div key={i} className="flex flex-col gap-1.5 px-3 py-2">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/2" />
                </div>
              ))
            : null}
          {events && events.length === 0 ? (
            <Empty
              size="sm"
              icon={History}
              title="No events yet"
              className="py-4"
            />
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
      </Card>
    </>
  );
}
