import { useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { trajectoryBounds } from "../lib/atif";
import { useApi } from "../lib/query";
import { TimeSpan } from "../evals/bits";
import { experimentCaseLink, runTitle } from "../lib/runs";
import type { AtifTrajectory, RunList } from "../lib/types";
import { CopyText, Tabs } from "../lib/ui";
import { RunTabs, type RunTab } from "./RunTabs";
import { splitRef } from "../lib/workspace";
import { useFollowWorkspace } from "../shell/workspace";
import { PageHeader } from "@/components/widgets/PageHeader";
import { buttonVariants } from "@/components/ui/button";

export function AgentRunPage() {
  const { agent = "", runId = "" } = useParams();
  useFollowWorkspace(splitRef(agent).workspace);
  const [tab, setTab] = useState<RunTab>("trajectory");
  // Trajectories are the largest payloads; drop them when the page closes.
  const loaded = useApi<AtifTrajectory>(
    `/agents/${encodeURIComponent(agent)}/runs/${encodeURIComponent(runId)}/trajectory`,
    { gcTime: 0 },
  );
  const trajectory = loaded.data ?? null;
  const error = loaded.error ? String(loaded.error) : null;

  // The trajectory carries no title; it lives on the run listing, so look it
  // up there (the same query the runs browser uses).
  const runs = useApi<RunList>(
    `/agents/${encodeURIComponent(agent)}/runs?query=${encodeURIComponent(runId)}&limit=20`,
  ).data?.runs;
  const title = runs ? runTitle(runs, runId) : null;
  const caseLink = runs ? experimentCaseLink(runs, runId) : null;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        breadcrumbs={[
          { label: "Runs", to: "/runs" },
          { label: agent, to: `/runs?agent=${encodeURIComponent(agent)}` },
        ]}
        title={
          <span className="font-mono">
            {title || <CopyText value={runId} />}
          </span>
        }
        actions={
          caseLink && (
            <Link className={buttonVariants({ variant: "link" })} to={caseLink}>
              Experiment run <ArrowUpRight size={12} />
            </Link>
          )
        }
      />
      <div className="page flex min-h-0 flex-1 flex-col">
        {(title || trajectory) && (
          <div className="toolbar mb-2">
            {title && (
              <span className="row-meta font-mono">
                <CopyText value={runId} />
              </span>
            )}
            {trajectory && (
              <TimeSpan
                {...trajectoryBounds(trajectory)}
                doneLabel="steps span"
                doneTitle="first to last step start; the last step's own time is not recorded"
              />
            )}
          </div>
        )}
        <Tabs<RunTab>
          tabs={[
            { id: "trajectory", label: "Trajectory" },
            { id: "workspace", label: "Workspace" },
            { id: "cost", label: "Cost" },
            { id: "timing", label: "Timing" },
          ]}
          active={tab}
          onSelect={setTab}
        />
        <div className="mt-3 flex min-h-0 flex-1 flex-col">
          <RunTabs
            tab={tab}
            trajectory={trajectory}
            error={error}
            agent={agent}
            runId={runId}
          />
        </div>
      </div>
    </div>
  );
}
