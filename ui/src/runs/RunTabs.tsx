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
    <div className="space-y-2">
      {Array.from({ length: 5 }, (_, i) => (
        <div
          key={i}
          className="skeleton h-10"
          style={{ width: `${100 - i * 7}%` }}
        />
      ))}
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
  if (error) return <div className="pane-status error">{error}</div>;
  if (!trajectory) return <TrajectorySkeleton />;
  if (tab === "cost") return <CostView doc={trajectory} />;
  if (tab === "timing") return <TimingView doc={trajectory} />;
  return <TrajectoryViewer doc={trajectory} />;
}
