import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { ColDef } from "ag-grid-community";
import { qs } from "../lib/api";
import { scoredBucketColumns } from "../lib/bucketColumns";
import {
  ColumnsMenu,
  DataGrid,
  readValue,
  renderCell,
  useDataGrid,
} from "../lib/DataGrid";
import {
  EXPERIMENT_LIST_LIMIT,
  STATUSES,
  listCapNote,
} from "../lib/experimentStatus";
import { useApi } from "../lib/query";
import type { ExperimentListItem } from "../lib/types";
import { experimentTailColumns } from "./experimentRow";
import { Select } from "../lib/Select";
import { splitRef } from "../lib/workspace";
import { useWorkspace } from "../shell/workspace";

const getExperimentId = (exp: ExperimentListItem) => String(exp.id);

export function ExperimentsPage() {
  const { workspace } = useWorkspace();
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("");
  const [agent, setAgent] = useState("");
  const listed = useApi<ExperimentListItem[]>(
    `/experiments${qs({ workspace: workspace.name, limit: String(EXPERIMENT_LIST_LIMIT) })}`,
  );
  const rows = listed.data ?? null;
  const error = listed.error ? String(listed.error) : null;

  useEffect(() => {
    setAgent("");
  }, [workspace.name]);

  const buckets = useMemo(() => scoredBucketColumns(rows ?? []), [rows]);
  const columns = useMemo<ColDef<ExperimentListItem>[]>(
    () => [
      {
        colId: "dataset",
        headerName: "dataset",
        type: "text",
        valueGetter: readValue((exp) => splitRef(exp.dataset).name),
        cellRenderer: renderCell<ExperimentListItem>((exp) => (
          <Link
            to={`/datasets/${encodeURIComponent(exp.dataset)}`}
            className="row-meta hover:text-brand"
            title={exp.dataset}
          >
            {splitRef(exp.dataset).name}
          </Link>
        )),
      },
      {
        colId: "number",
        headerName: "#",
        headerTooltip: "number in its dataset",
        type: "number",
        initialWidth: 72,
        field: "number",
        cellRenderer: renderCell<ExperimentListItem>((exp) => (
          <Link
            to={`/experiments/${exp.id}`}
            className="font-mono font-medium text-brand hover:underline"
            title={`experiment ${exp.id}`}
          >
            #{exp.number}
          </Link>
        )),
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
  const grid = useDataGrid<ExperimentListItem>("experiments");

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
            : `${grid.filteredCount ?? visible.length} experiments${listCapNote(rows?.length ?? 0) ? ` (of the latest ${EXPERIMENT_LIST_LIMIT})` : ""}`}
        </span>
        <ColumnsMenu grid={grid} />
      </div>
      {error && <div className="pane-status error">{error}</div>}
      <DataGrid
        grid={grid}
        rows={visible}
        columns={columns}
        getRowId={getExperimentId}
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
