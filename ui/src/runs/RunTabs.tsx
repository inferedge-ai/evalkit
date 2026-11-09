import { Skeleton } from "@/components/ui/skeleton";
import type { AtifTrajectory } from "../lib/types";
import { CostView } from "./CostView";
import { TimingView } from "./TimingView";
import { TrajectoryViewer } from "./TrajectoryViewer";
import { WorkspaceTab } from "./WorkspaceTab";

export type RunTab = "trajectory" | "workspace" | "cost" | "timing";

// The tabs that render from the ATIF trajectory doc (workspace fetches its own).
export const TRAJECTORY_TABS: RunTab[] = ["trajectory", "cost", "timing"];

function TrajectorySkeleton() {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      <Skeleton className="h-4 w-80 max-w-full" />
      <div className="flex min-h-96 flex-1 overflow-hidden rounded-lg border border-line">
        <div className="min-w-0 flex-1 space-y-2 p-4">
          {Array.from({ length: 4 }, (_, i) => (
            <div key={i} className="flex gap-3">
              <Skeleton className="h-8 w-14 shrink-0" />
              <Skeleton className="h-20 flex-1" />
            </div>
          ))}
        </div>
        <div className="w-64 shrink-0 space-y-2 border-l border-line bg-surface p-2">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton key={i} className="h-8" />
          ))}
        </div>
      </div>
    </div>
  );
}

function AnalysisSkeleton() {
  return (
    <div className="flex max-w-[68rem] flex-col gap-4">
      <div className="tile-grid">
        {Array.from({ length: 3 }, (_, i) => (
          <Skeleton key={i} className="h-14" />
        ))}
      </div>
      <Skeleton className="h-48 rounded-xl" />
    </div>
  );
}

// The trajectory/cost/timing/workspace tab bodies, shared by the agent-run and
// case-run pages. The trajectory viewer fills the remaining height (its parent
// must be a flex column); cost/timing/workspace take their natural height.
export function RunTabs({
  tab,
  trajectory,
  error,
  agent,
  runId,
}: {
  tab: string;
  trajectory: AtifTrajectory | null;
  error: string | null;
  agent: string | undefined;
  runId: string | null;
}) {
  if (tab === "workspace")
    return agent && runId ? <WorkspaceTab agent={agent} runId={runId} /> : null;
  if (!TRAJECTORY_TABS.includes(tab as RunTab)) return null;
  if (!trajectory) {
    if (error) return <div className="pane-status error">{error}</div>;
    return tab === "trajectory" ? <TrajectorySkeleton /> : <AnalysisSkeleton />;
  }
  return (
    <>
      {error && <div className="pane-status error">{error}</div>}
      {tab === "cost" ? (
        <CostView doc={trajectory} />
      ) : tab === "timing" ? (
        <TimingView doc={trajectory} />
      ) : (
        <TrajectoryViewer doc={trajectory} />
      )}
    </>
  );
}
