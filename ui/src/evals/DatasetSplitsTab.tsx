import { Link } from "react-router-dom";
import type { SplitInfo } from "../lib/types";

export function DatasetSplitsTab({
  splits,
  dataset,
}: {
  splits: SplitInfo[];
  dataset: string;
}) {
  if (splits.length === 0)
    return (
      <div className="pane-status">No tags on this dataset's cases yet.</div>
    );
  return (
    <div className="card">
      <div className="rows">
        {splits.map((split) => (
          <Link
            key={split.name}
            className="row grid-cols-[minmax(0,1fr)_auto] hover:bg-fill"
            to={`/datasets/${encodeURIComponent(dataset)}/cases?tag=${encodeURIComponent(split.name)}`}
          >
            <span className="font-mono">{split.name}</span>
            <span className="row-meta num">{split.count} cases</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
