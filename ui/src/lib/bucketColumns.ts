// A bucket column on the score tables. `bucket` keys both the per-example
// history points and an experiment row's bucket means; `label` heads wide
// tables and `short` heads narrow ones.
export interface BucketColumn {
  bucket: string;
  label: string;
  short: string;
}

export const BUCKET_COLUMNS: BucketColumn[] = [
  {
    bucket: "content",
    label: "answer",
    short: "ans",
  },
  {
    bucket: "presentation",
    label: "presentation",
    short: "pres",
  },
  {
    bucket: "trajectory",
    label: "trajectory",
    short: "traj",
  },
];

const LIST_BUCKET_COLUMNS: BucketColumn[] = [
  ...BUCKET_COLUMNS,
  { bucket: "efficiency", label: "efficiency", short: "eff" },
  { bucket: "custom", label: "custom", short: "custom" },
];

export function scoredBucketColumns(
  rows: { scores: Record<string, number | null> }[],
): BucketColumn[] {
  return LIST_BUCKET_COLUMNS.filter((column) =>
    rows.some((row) => scoreValue(row.scores, column.bucket) !== null),
  );
}

export const EXPERIMENT_COST_KEY = "gauge_agent_cost_usd";

export function scoreValue(
  scores: Record<string, number | null> | null | undefined,
  key: string,
): number | null {
  const v = scores?.[key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}
