import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, qs } from "../lib/api";
import { DataTable, useDataTable } from "../lib/DataTable";
import type { Column } from "../lib/DataTable";
import { fmtWhen } from "../lib/format";
import type { QueueItem } from "../lib/types";
import { splitRef } from "../lib/workspace";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";

type View = "mine" | "all" | "changes";

const VIEWS: { id: View; label: string }[] = [
  { id: "mine", label: "requested to me" },
  { id: "all", label: "all under review" },
  { id: "changes", label: "changes requested" },
];

const NO_ITEMS: QueueItem[] = [];

function queueColumns(changes: boolean): Column<QueueItem>[] {
  return [
    {
      id: "dataset",
      header: "dataset",
      accessorFn: (i) => splitRef(i.dataset).name,
    },
    {
      id: "case",
      header: "case",
      accessorKey: "case_id",
      cell: (c) => (
        <Link
          className="font-mono text-brand hover:underline"
          to={`/datasets/${encodeURIComponent(c.row.original.dataset)}/cases/${encodeURIComponent(c.row.original.case_id)}`}
        >
          {c.row.original.case_id}
        </Link>
      ),
    },
    {
      id: "requested_by",
      header: changes ? "reviewer" : "requested by",
      accessorKey: "requested_by",
    },
    ...(changes
      ? []
      : [
          {
            id: "requested_to",
            header: "to",
            accessorFn: (i) => i.requested_to ?? "—",
          } satisfies Column<QueueItem>,
        ]),
    {
      id: "requested_at",
      header: changes ? "sent back" : "requested",
      accessorFn: (i) => i.requested_at ?? "",
      cell: (c) => (
        <span className="row-meta">{fmtWhen(c.row.original.requested_at)}</span>
      ),
    },
    {
      id: "question",
      header: "question",
      accessorKey: "input",
      meta: { wrap: true },
      cell: (c) => (
        <span className="line-clamp-3 text-ink-3">{c.row.original.input}</span>
      ),
    },
  ];
}

export function ReviewsPage() {
  const me = useMe();
  const { workspace } = useWorkspace();
  const [view, setView] = useState<View>("mine");
  const [items, setItems] = useState<QueueItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stale = false;
    setItems(null);
    setError(null);
    api<{ items: QueueItem[] }>(
      `/review/queue${qs({
        reviewer: view === "mine" ? me.username : undefined,
        state: view === "changes" ? "changes_requested" : undefined,
        workspace: workspace.name,
      })}`,
    )
      .then((r) => !stale && setItems(r.items))
      .catch((e) => !stale && setError(String(e)));
    return () => {
      stale = true;
    };
  }, [view, me.username, workspace.name]);

  const changes = view === "changes";
  const columns = useMemo(() => queueColumns(changes), [changes]);
  const table = useDataTable({
    id: "review-queue",
    data: items ?? NO_ITEMS,
    columns,
    getRowId: (i) => `${i.dataset}/${i.case_id}`,
  });

  return (
    <div className="page">
      <div className="toolbar mb-2">
        <h1 className="page-title">Reviews</h1>
        <div className="ml-4 flex flex-wrap items-center gap-3">
          {VIEWS.map((v) => (
            <label key={v.id} className="row-meta flex items-center gap-1">
              <input
                type="radio"
                checked={view === v.id}
                onChange={() => setView(v.id)}
              />
              {v.label}
            </label>
          ))}
        </div>
        <span className="row-meta num ml-auto">
          {error
            ? ""
            : items
              ? `${items.length} ${changes ? "awaiting an edit" : "waiting"}`
              : "Loading…"}
        </span>
      </div>
      {error && <div className="pane-status error">{error}</div>}
      {!error && (
        <DataTable
          table={table}
          loading={!items}
          empty={
            workspace.type === "personal"
              ? "Personal datasets have no review state."
              : changes
                ? "No cases are waiting on their author."
                : "Nothing waiting for review."
          }
        />
      )}
    </div>
  );
}
