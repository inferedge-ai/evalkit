import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, qs } from "../lib/api";
import { scoredBucketColumns } from "../lib/bucketColumns";
import { ColumnsMenu, DataTable, useDataTable } from "../lib/DataTable";
import type { Column } from "../lib/DataTable";
import {
  EXPERIMENT_LIST_LIMIT,
  STATUSES,
  listCapNote,
} from "../lib/experimentStatus";
import type { ExperimentListItem } from "../lib/types";
import { experimentTailColumns } from "./experimentRow";
import { Select } from "../lib/Select";
import { splitRef } from "../lib/workspace";
import { useWorkspace } from "../shell/workspace";

const HIDDEN = ["latency", "took", "rev", "judge"];

export function ExperimentsPage() {
  const { workspace } = useWorkspace();
  const [rows, setRows] = useState<ExperimentListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [agent, setAgent] = useState("");

  useEffect(() => {
    let stale = false;
    setRows(null);
    setError(null);
    setAgent("");
    api<ExperimentListItem[]>(
      `/experiments${qs({ workspace: workspace.name, limit: String(EXPERIMENT_LIST_LIMIT) })}`,
    )
      .then((list) => !stale && setRows(list))
      .catch((e) => !stale && setError(String(e)));
    return () => {
      stale = true;
    };
  }, [workspace.name]);

  const buckets = useMemo(() => scoredBucketColumns(rows ?? []), [rows]);
  const columns = useMemo<Column<ExperimentListItem>[]>(
    () => [
      {
        id: "dataset",
        header: "dataset",
        accessorFn: (exp) => splitRef(exp.dataset).name,
        meta: { wrap: true },
        cell: (c) => (
          <Link
            to={`/datasets/${encodeURIComponent(c.row.original.dataset)}`}
            className="row-meta hover:text-brand"
            title={c.row.original.dataset}
          >
            {c.getValue()}
          </Link>
        ),
      },
      {
        id: "number",
        header: "#",
        accessorKey: "number",
        meta: { align: "right", title: "number in its dataset" },
        cell: (c) => (
          <Link
            to={`/experiments/${c.row.original.id}`}
            className="font-mono font-medium text-brand hover:underline"
            title={`experiment ${c.row.original.id}`}
          >
            #{c.row.original.number}
          </Link>
        ),
      },
      ...experimentTailColumns(buckets),
    ],
    [buckets],
  );
  const loading = !rows && !error;
  const agents = [...new Set((rows ?? []).map((exp) => exp.agent))].sort();
  const visible = (rows ?? []).filter((exp) => {
    if (
      query &&
      !`#${exp.number} ${exp.id} ${exp.dataset}`
        .toLowerCase()
        .includes(query.toLowerCase())
    )
      return false;
    if (status && exp.status !== status) return false;
    if (agent && exp.agent !== agent) return false;
    return true;
  });
  const table = useDataTable({
    id: "experiments",
    data: visible,
    columns,
    getRowId: (exp) => String(exp.id),
    defaultHidden: HIDDEN,
  });

  return (
    <div className="page">
      <div className="toolbar mb-3">
        <h1 className="page-title">Experiments</h1>
        <input
          className="input w-64"
          placeholder="Filter name or dataset"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <Select
          ariaLabel="status"
          value={status}
          onChange={setStatus}
          options={[
            { value: "", label: "All statuses" },
            ...STATUSES.map((s) => ({ value: s, label: s })),
          ]}
        />
        <Select
          ariaLabel="agent"
          value={agent}
          onChange={setAgent}
          options={[
            { value: "", label: "All agents" },
            ...agents.map((a) => ({ value: a, label: a })),
          ]}
        />
        <span
          className="row-meta ml-auto"
          title={listCapNote(rows?.length ?? 0) ?? undefined}
        >
          {loading
            ? "Loading…"
            : `${visible.length} experiments${listCapNote(rows?.length ?? 0) ? ` (of the latest ${EXPERIMENT_LIST_LIMIT})` : ""}`}
        </span>
        <ColumnsMenu table={table} defaultHidden={HIDDEN} />
      </div>
      {error && <div className="pane-status error">{error}</div>}
      <DataTable
        table={table}
        loading={loading}
        empty={
          rows && rows.length === 0
            ? `No experiments in @${workspace.name} yet.`
            : "No experiments match this filter."
        }
      />
    </div>
  );
}
