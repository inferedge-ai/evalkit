import { Skeleton } from "@/components/ui/skeleton";
import { useMemo } from "react";
import { Link } from "react-router-dom";
import type { ColDef } from "ag-grid-community";
import { scoredBucketColumns } from "../lib/bucketColumns";
import {
  ColumnsMenu,
  DataGrid,
  renderCell,
  useDataGrid,
} from "../lib/DataGrid";
import { EXPERIMENT_LIST_LIMIT, listCapNote } from "../lib/experimentStatus";
import { useApi } from "../lib/query";
import type { ExperimentListItem } from "../lib/types";
import { experimentTailColumns } from "./experimentRow";

const getExperimentId = (exp: ExperimentListItem) => String(exp.id);

export function DatasetExperimentsTab({ dataset }: { dataset: string }) {
  const listed = useApi<ExperimentListItem[]>(
    `/datasets/${encodeURIComponent(dataset)}/experiments?limit=${EXPERIMENT_LIST_LIMIT}`,
  );
  const experiments = listed.data ?? null;
  const error = listed.error ? String(listed.error) : null;
  const buckets = useMemo(
    () => scoredBucketColumns(experiments ?? []),
    [experiments],
  );
  const columns = useMemo<ColDef<ExperimentListItem>[]>(
    () => [
      {
        colId: "number",
        headerName: "#",
        headerTooltip: "number in its dataset",
        type: "number",
        initialWidth: 72,
        field: "number",
        cellRenderer: renderCell<ExperimentListItem>((exp) => (
          <Link
            className="font-mono font-medium text-brand hover:underline"
            to={`/experiments/${exp.id}`}
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
  const grid = useDataGrid<ExperimentListItem>("dataset-experiments");

  if (error) return <div className="pane-status error">{error}</div>;
  if (!experiments)
    return (
      <div className="card rows">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="row grid-cols-[1fr_auto]">
            <Skeleton className="h-4" style={{ width: `${25 + i * 7}%` }} />
            <Skeleton className="h-3 w-40" />
          </div>
        ))}
      </div>
    );
  if (experiments.length === 0)
    return (
      <div className="pane-status">
        No experiments yet — run one with the button above or{" "}
        <span className="font-mono">
          evalkit run {dataset} --agent &lt;agent&gt;
        </span>
        .
      </div>
    );

  const capNote = listCapNote(experiments.length);
  return (
    <>
      <div className="toolbar mb-2">
        {capNote && <p className="row-meta">{capNote}</p>}
        <span className="ml-auto">
          <ColumnsMenu grid={grid} />
        </span>
      </div>
      <DataGrid
        grid={grid}
        rows={experiments}
        columns={columns}
        getRowId={getExperimentId}
      />
    </>
  );
}
