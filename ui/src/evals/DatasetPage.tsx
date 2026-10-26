import { useEffect, useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { api, isMissing } from "../lib/api";
import { fmtWhen } from "../lib/format";
import type { Dataset, SplitInfo } from "../lib/types";
import { Tabs } from "../lib/ui";
import { NotFound } from "../shell/NotFound";
import { DatasetCasesTab } from "./DatasetCasesTab";
import { DatasetExperimentsTab } from "./DatasetExperimentsTab";
import { DatasetSplitsTab } from "./DatasetSplitsTab";
import { RunLauncher } from "./RunLauncher";
import { splitRef } from "../lib/workspace";
import { useFollowWorkspace } from "../shell/workspace";

type Tab = "experiments" | "cases" | "splits";

const TABS: { id: Tab; label: string }[] = [
  { id: "experiments", label: "Experiments" },
  { id: "cases", label: "Cases" },
  { id: "splits", label: "Splits" },
];

export function syncLabel(ds: Dataset): string[] {
  const parts = [
    ds.workspace_is_shared ? `@${ds.workspace}` : `${ds.workspace}'s workspace`,
  ];
  const sync = ds.last_sync;
  if (!sync) return parts;
  if (sync.detail.branch) parts.push(sync.detail.branch);
  if (sync.detail.sha) parts.push(sync.detail.sha.slice(0, 8));
  if (sync.detail.dirty) parts.push("dirty");
  if (typeof sync.detail.row_count === "number")
    parts.push(`${sync.detail.row_count} rows`);
  parts.push(`synced by ${sync.detail.actor ?? sync.actor}`);
  const when = fmtWhen(sync.created_at);
  if (when) parts.push(when);
  return parts;
}

export function DatasetPage() {
  const { ref = "", tab = "experiments" } = useParams();
  useFollowWorkspace(splitRef(ref).workspace);
  const navigate = useNavigate();
  const [splits, setSplits] = useState<SplitInfo[]>([]);
  const [info, setInfo] = useState<Dataset | null>(null);
  const [missing, setMissing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [launch, setLaunch] = useState<{ caseIds?: string[] } | null>(null);

  const fullRef = ref.includes("@");
  useEffect(() => {
    setMissing(false);
    if (!fullRef) return;
    api<Dataset>(`/datasets/${encodeURIComponent(ref)}`)
      .then(setInfo)
      .catch((e) => (isMissing(e) ? setMissing(true) : setError(String(e))));
    api<SplitInfo[]>(`/datasets/${encodeURIComponent(ref)}/splits`)
      .then(setSplits)
      .catch(() => setSplits([]));
  }, [ref, fullRef]);

  if (missing || !fullRef)
    return (
      <NotFound
        what={`dataset ${ref}`}
        up={{ label: "All datasets", to: "/datasets" }}
      />
    );
  if (!TABS.some((t) => t.id === tab))
    return (
      <Navigate
        to={`/datasets/${encodeURIComponent(ref)}/experiments`}
        replace
      />
    );
  const metaParts = info ? syncLabel(info) : [];

  return (
    <div className="page">
      <div className="toolbar mb-2">
        <h1 className="page-title font-mono">{ref}</h1>
        {metaParts.length > 0 && (
          <span className="row-meta truncate" title={metaParts.join(" · ")}>
            {metaParts.join(" · ")}
          </span>
        )}
        <span className="ml-auto">
          {info?.workspace_is_shared ? (
            <button className="btn btn-primary" onClick={() => setLaunch({})}>
              Run experiment
            </button>
          ) : (
            <span
              className="row-meta font-mono"
              title="workspace datasets run from the CLI"
            >
              evalkit run {ref} --agent &lt;agent&gt;
            </span>
          )}
        </span>
      </div>
      {error && <div className="pane-status error">{error}</div>}
      <Tabs<Tab>
        tabs={TABS}
        active={tab as Tab}
        onSelect={(t) => navigate(`/datasets/${encodeURIComponent(ref)}/${t}`)}
      />
      <div className="mt-3">
        {tab === "cases" && (
          <DatasetCasesTab
            dataset={ref}
            reviewed={!!info?.workspace_is_shared && info.kind === "suite"}
            onRun={(caseIds) => setLaunch({ caseIds })}
          />
        )}
        {tab === "experiments" && <DatasetExperimentsTab dataset={ref} />}
        {tab === "splits" && <DatasetSplitsTab splits={splits} dataset={ref} />}
      </div>
      {launch && (
        <RunLauncher
          dataset={ref}
          splits={splits}
          initialCaseIds={launch.caseIds}
          onClose={() => setLaunch(null)}
        />
      )}
    </div>
  );
}
