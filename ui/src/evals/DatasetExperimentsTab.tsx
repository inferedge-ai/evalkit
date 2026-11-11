import { useMemo } from "react";
import { FlaskConical } from "lucide-react";
import { Link } from "react-router-dom";
import { Empty } from "@/components/widgets/Empty";
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
import { Card } from "@/components/ui/card";

const getExperimentId = (exp: ExperimentListItem) => String(exp.id);

const NO_EXPERIMENTS: ExperimentListItem[] = [];

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
  if (experiments?.length === 0)
    return (
      <Card className="py-8">
        <Empty
          icon={FlaskConical}
          title="No experiments on this dataset yet"
          description={
            <>
              Start one with Run experiment, or run{" "}
              <span className="font-mono">
                evalkit run {dataset} --agent &lt;agent&gt;
              </span>
              .
            </>
          }
        />
      </Card>
    );

  const capNote = experiments ? listCapNote(experiments.length) : null;
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
        rows={experiments ?? NO_EXPERIMENTS}
        columns={columns}
        getRowId={getExperimentId}
        loading={!experiments}
      />
    </>
  );
}
