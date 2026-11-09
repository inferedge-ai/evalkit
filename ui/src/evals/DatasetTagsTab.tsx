import { Link } from "react-router-dom";
import type { TagInfo } from "../lib/types";
import { Card } from "@/components/ui/card";

export function DatasetTagsTab({
  tags,
  dataset,
}: {
  tags: TagInfo[];
  dataset: string;
}) {
  if (tags.length === 0)
    return (
      <div className="pane-status">No tags on this dataset's cases yet.</div>
    );
  return (
    <Card className="gap-0 py-0">
      <div className="rows">
        {tags.map((tag) => (
          <Link
            key={tag.name}
            className="row grid-cols-[minmax(0,1fr)_auto] hover:bg-fill"
            to={`/datasets/${encodeURIComponent(dataset)}/cases?tag=${encodeURIComponent(tag.name)}`}
          >
            <span className="font-mono">{tag.name}</span>
            <span className="row-meta num">{tag.count} cases</span>
          </Link>
        ))}
      </div>
    </Card>
  );
}
