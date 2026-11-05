import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { scoredBucketColumns } from "../lib/bucketColumns";
import { ColumnsMenu, DataTable, useDataTable } from "../lib/DataTable";
import type { Column } from "../lib/DataTable";
import { EXPERIMENT_LIST_LIMIT, listCapNote } from "../lib/experimentStatus";
import type { ExperimentListItem } from "../lib/types";
import { experimentTailColumns } from "./experimentRow";

const HIDDEN = ["latency", "took", "rev", "judge"];

const NONE: ExperimentListItem[] = [];

export function DatasetExperimentsTab({ dataset }: { dataset: string }) {
  const [experiments, setExperiments] = useState<ExperimentListItem[] | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const buckets = useMemo(
    () => scoredBucketColumns(experiments ?? []),
    [experiments],
  );
  const columns = useMemo<Column<ExperimentListItem>[]>(
    () => [
      {
        id: "number",
        header: "#",
        accessorKey: "number",
        meta: { align: "right", title: "number in its dataset" },
        cell: (c) => (
          <Link
            className="font-mono font-medium text-brand hover:underline"
            to={`/experiments/${c.row.original.id}`}
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
  const table = useDataTable({
    id: "dataset-experiments",
    data: experiments ?? NONE,
    columns,
    getRowId: (exp) => String(exp.id),
    defaultHidden: HIDDEN,
  });

  useEffect(() => {
    api<ExperimentListItem[]>(
      `/datasets/${encodeURIComponent(dataset)}/experiments?limit=${EXPERIMENT_LIST_LIMIT}`,
    )
      .then(setExperiments)
      .catch((e) => setError(String(e)));
  }, [dataset]);

  if (error) return <div className="pane-status error">{error}</div>;
  if (!experiments)
    return (
      <div className="card rows">
        {Array.from({ length: 5 }, (_, i) => (
          <div key={i} className="row grid-cols-[1fr_auto]">
            <span
              className="skeleton h-4"
              style={{ width: `${25 + i * 7}%` }}
            />
            <span className="skeleton h-3 w-40" />
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
          <ColumnsMenu table={table} defaultHidden={HIDDEN} />
        </span>
      </div>
      <DataTable table={table} />
    </>
  );
}
