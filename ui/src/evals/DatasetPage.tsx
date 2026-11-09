import { useState } from "react";
import { Navigate, useNavigate, useParams } from "react-router-dom";
import { isMissing } from "../lib/api";
import { fmtWhen } from "../lib/format";
import { useApi } from "../lib/query";
import type { Dataset, TagInfo } from "../lib/types";
import { Tabs } from "../lib/ui";
import { NotFound } from "../shell/NotFound";
import { DatasetCasesTab } from "./DatasetCasesTab";
import { DatasetExperimentsTab } from "./DatasetExperimentsTab";
import { DatasetTagsTab } from "./DatasetTagsTab";
import { RunLauncher } from "./RunLauncher";
import { splitRef } from "../lib/workspace";
import { useFollowWorkspace } from "../shell/workspace";
import { PageHeader } from "@/components/widgets/PageHeader";

type Tab = "experiments" | "cases" | "tags";

const TABS: { id: Tab; label: string }[] = [
  { id: "experiments", label: "Experiments" },
  { id: "cases", label: "Cases" },
  { id: "tags", label: "Tags" },
];

const NO_TAGS: TagInfo[] = [];

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

// A fresh page per dataset, so the run launcher left open on one can't start
// runs on the next after Back or Forward.
export function DatasetPage() {
  const { ref } = useParams();
  return <DatasetView key={ref} />;
}

function DatasetView() {
  const { ref = "", tab = "experiments" } = useParams();
  useFollowWorkspace(splitRef(ref).workspace);
  const navigate = useNavigate();
  const [launch, setLaunch] = useState<{ caseIds?: string[] } | null>(null);

  const fullRef = ref.includes("@");
  const base = fullRef ? `/datasets/${encodeURIComponent(ref)}` : null;
  const loaded = useApi<Dataset>(base);
  const tags = useApi<TagInfo[]>(base ? `${base}/tags` : null).data ?? NO_TAGS;
  const info = loaded.data ?? null;
  const missing = isMissing(loaded.error);
  const error = loaded.error && !missing ? String(loaded.error) : null;

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
    <>
      <PageHeader
        breadcrumbs={[{ label: "Datasets", to: "/datasets" }]}
        title={<span className="font-mono">{ref}</span>}
        actions={
          info && (
            <button className="btn btn-primary" onClick={() => setLaunch({})}>
              Run experiment
            </button>
          )
        }
      />
      <div className="page">
        <div className="toolbar mb-2">
          {metaParts.length > 0 && (
            <span className="row-meta truncate" title={metaParts.join(" · ")}>
              {metaParts.join(" · ")}
            </span>
          )}
        </div>
        {error && <div className="pane-status error">{error}</div>}
        <Tabs<Tab>
          tabs={TABS}
          active={tab as Tab}
          onSelect={(t) =>
            navigate(`/datasets/${encodeURIComponent(ref)}/${t}`)
          }
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
          {tab === "tags" && <DatasetTagsTab tags={tags} dataset={ref} />}
        </div>
        {launch && (
          <RunLauncher
            dataset={ref}
            tags={tags}
            initialCaseIds={launch.caseIds}
            serverRuns={!!info?.workspace_is_shared}
            onClose={() => setLaunch(null)}
          />
        )}
      </div>
    </>
  );
}
