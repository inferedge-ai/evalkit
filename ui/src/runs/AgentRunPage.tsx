import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { trajectoryBounds } from "../lib/atif";
import { TimeSpan } from "../evals/bits";
import { experimentCaseLink, runTitle } from "../lib/runs";
import type { AtifTrajectory, RunList } from "../lib/types";
import { CopyText, Tabs } from "../lib/ui";
import { RunTabs, type RunTab } from "./RunTabs";
import { splitRef } from "../lib/workspace";
import { useFollowWorkspace } from "../shell/workspace";

export function AgentRunPage() {
  const { agent = "", runId = "" } = useParams();
  useFollowWorkspace(splitRef(agent).workspace);
  const [title, setTitle] = useState<string | null>(null);
  const [caseLink, setCaseLink] = useState<string | null>(null);
  const [tab, setTab] = useState<RunTab>("trajectory");
  const [trajectory, setTrajectory] = useState<AtifTrajectory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTrajectory(null);
    setError(null);
    api<AtifTrajectory>(
      `/agents/${encodeURIComponent(agent)}/runs/${encodeURIComponent(runId)}/trajectory`,
    )
      .then(setTrajectory)
      .catch((e) => setError(String(e)));
  }, [agent, runId]);

  // The trajectory carries no title; it lives on the run listing, so look it
  // up there (the same query the runs browser uses).
  useEffect(() => {
    setTitle(null);
    setCaseLink(null);
    api<RunList>(
      `/agents/${encodeURIComponent(agent)}/runs?query=${encodeURIComponent(runId)}&limit=20`,
    )
      .then((list) => {
        setTitle(runTitle(list.runs, runId));
        setCaseLink(experimentCaseLink(list.runs, runId));
      })
      .catch(() => setTitle(null));
  }, [agent, runId]);

  return (
    <div className="page flex h-full flex-col">
      <div className="toolbar mb-2">
        <h1 className="max-w-160 truncate font-mono text-head font-semibold">
          {title || <CopyText value={runId} />}
        </h1>
        {title && (
          <span className="row-meta font-mono">
            <CopyText value={runId} />
          </span>
        )}
        <span className="chip muted">{agent}</span>
        {trajectory && (
          <TimeSpan
            {...trajectoryBounds(trajectory)}
            doneLabel="steps span"
            doneTitle="first to last step start; the last step's own time is not recorded"
          />
        )}
        {caseLink && (
          <Link className="btn btn-quiet ml-auto text-brand" to={caseLink}>
            Experiment run ↗
          </Link>
        )}
        <Link
          className={`btn btn-quiet text-brand${caseLink ? "" : " ml-auto"}`}
          to={`/runs?agent=${agent}`}
        >
          Runs ↗
        </Link>
      </div>
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
  );
}
