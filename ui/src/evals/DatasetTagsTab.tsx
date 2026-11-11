import { Tags } from "lucide-react";
import { Link } from "react-router-dom";
import type { TagInfo } from "../lib/types";
import { Card } from "@/components/ui/card";
import { Empty } from "@/components/widgets/Empty";

export function DatasetTagsTab({
  tags,
  dataset,
}: {
  tags: TagInfo[];
  dataset: string;
}) {
  if (tags.length === 0)
    return (
      <Card className="py-8">
        <Empty
          icon={Tags}
          title="No tags on this dataset's cases yet"
          description="A case gets tags from its YAML, its folder in the suite, or an overlay."
        />
      </Card>
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
