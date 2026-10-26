import { progressLabel } from "../lib/experimentStatus";
import type { Progress } from "../lib/types";

export function ProgressBar({ progress }: { progress: Progress }) {
  const total = Math.max(progress.total, 1);
  const ran = (100 * progress.ran) / total;
  const evaluated = (100 * progress.evaluated) / total;
  return (
    <span className="flex items-center gap-2" title={progressLabel(progress)}>
      <span
        className="relative h-2 w-40 overflow-hidden rounded bg-line-soft"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={progress.total}
        aria-valuenow={progress.evaluated}
      >
        <span
          className="absolute inset-y-0 left-0 bg-info-bg"
          style={{ width: `${ran}%` }}
        />
        <span
          className="absolute inset-y-0 left-0 bg-brand"
          style={{ width: `${evaluated}%` }}
        />
      </span>
      <span className="row-meta num">{progressLabel(progress)}</span>
    </span>
  );
}
