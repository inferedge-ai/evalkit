import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ColDef } from "ag-grid-community";
import { qs } from "../lib/api";
import { useApi } from "../lib/query";
import {
  ColumnsMenu,
  DataGrid,
  readValue,
  renderCell,
  useDataGrid,
} from "../lib/DataGrid";
import { fmtWhen } from "../lib/format";
import type { QueueItem } from "../lib/types";
import { Tabs } from "../lib/ui";
import { splitRef } from "../lib/workspace";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";
import { PageHeader } from "@/components/widgets/PageHeader";

type View = "mine" | "all" | "changes";

const VIEWS: { id: View; label: string }[] = [
  { id: "mine", label: "Requested to me" },
  { id: "all", label: "All under review" },
  { id: "changes", label: "Changes requested" },
];

const NO_ITEMS: QueueItem[] = [];

const getItemId = (i: QueueItem) => `${i.dataset}/${i.case_id}`;

function queueColumns(
  changes: boolean,
  everyWorkspace: boolean,
): ColDef<QueueItem>[] {
  return [
    {
      colId: "dataset",
      headerName: "dataset",
      type: "text",
      valueGetter: readValue((i) =>
        everyWorkspace ? i.dataset : splitRef(i.dataset).name,
      ),
    },
    {
      colId: "case",
      headerName: "case",
      type: "text",
      field: "case_id",
      cellRenderer: renderCell<QueueItem>((i) => (
        <Link
          className="font-mono text-brand hover:underline"
          to={`/datasets/${encodeURIComponent(i.dataset)}/cases/${encodeURIComponent(i.case_id)}`}
          title={i.case_id}
        >
          {i.case_id}
        </Link>
      )),
    },
    {
      colId: "requested_by",
      headerName: changes ? "reviewer" : "requested by",
      type: "text",
      field: "requested_by",
    },
    ...(changes
      ? []
      : [
          {
            colId: "requested_to",
            headerName: "to",
            type: "text",
            valueGetter: readValue<QueueItem, string>(
              (i) => i.requested_to ?? "—",
            ),
          } satisfies ColDef<QueueItem>,
        ]),
    {
      colId: "requested_at",
      headerName: changes ? "sent back" : "requested",
      valueGetter: readValue((i) => i.requested_at ?? ""),
      cellRenderer: renderCell<QueueItem>((i) => (
        <span className="row-meta">{fmtWhen(i.requested_at)}</span>
      )),
    },
    {
      colId: "question",
      headerName: "question",
      type: "text",
      initialFlex: 2,
      field: "input",
      cellRenderer: renderCell<QueueItem>((i) => (
        <span className="text-ink-3" title={i.input}>
          {i.input}
        </span>
      )),
    },
  ];
}

export function ReviewsPage() {
  const me = useMe();
  const { workspace } = useWorkspace();
  const [view, setView] = useState<View>("mine");
  const everyWorkspace = workspace.type === "personal";
  const queue = useApi<{ items: QueueItem[] }>(
    `/review/queue${qs({
      reviewer: view === "mine" ? me.username : undefined,
      state: view === "changes" ? "changes_requested" : undefined,
      workspace: everyWorkspace ? undefined : workspace.name,
    })}`,
  );
  const items = queue.data?.items ?? null;
  const error = queue.error ? String(queue.error) : null;

  const changes = view === "changes";
  const columns = useMemo(
    () => queueColumns(changes, everyWorkspace),
    [changes, everyWorkspace],
  );
  const grid = useDataGrid<QueueItem>("review-queue");

  return (
    <>
      <PageHeader title="Reviews" />
      <div className="page">
        <Tabs<View> tabs={VIEWS} active={view} onSelect={setView} />
        <div className="toolbar mt-3 mb-2">
          {everyWorkspace && (
            <span className="row-meta">across every workspace</span>
          )}
          <span className="row-meta num ml-auto">
            {error
              ? ""
              : items
                ? `${grid.filteredCount ?? items.length} ${changes ? "awaiting an edit" : "waiting"}`
                : "Loading…"}
          </span>
          <ColumnsMenu grid={grid} />
        </div>
        {error && <div className="pane-status error">{error}</div>}
        {!error && (
          <DataGrid
            grid={grid}
            rows={items ?? NO_ITEMS}
            columns={columns}
            getRowId={getItemId}
            loading={!items}
            empty={
              changes
                ? "No cases are waiting on their author."
                : "Nothing waiting for review."
            }
          />
        )}
      </div>
    </>
  );
}
