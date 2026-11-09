import { useState } from "react";
import { Link } from "react-router-dom";
import { canWrite } from "../lib/access";
import { qs } from "../lib/api";
import { useApi } from "../lib/query";
import { fmtWhen } from "../lib/format";
import type { Dataset } from "../lib/types";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";
import { PageHeader } from "@/components/widgets/PageHeader";

function datasetMeta(ds: Dataset): string {
  const parts: string[] = [];
  const sync = ds.last_sync;
  if (!sync) return "";
  if (sync.detail.sha) parts.push(sync.detail.sha.slice(0, 8));
  if (sync.detail.branch) parts.push(sync.detail.branch);
  if (sync.detail.dirty) parts.push("dirty");
  if (typeof sync.detail.row_count === "number")
    parts.push(`${sync.detail.row_count} rows`);
  const when = fmtWhen(sync.created_at);
  if (when) parts.push(when);
  return parts.join(" · ");
}

function DatasetRow({ ds }: { ds: Dataset }) {
  return (
    <Link
      className="row grid-cols-[minmax(0,1fr)_auto_minmax(0,18rem)]"
      to={`/datasets/${encodeURIComponent(ds.ref)}`}
    >
      <span className="row-strong">{ds.name}</span>
      <span className="chip muted">{ds.kind}</span>
      <span
        className="row-meta num truncate text-right"
        title={datasetMeta(ds)}
      >
        {datasetMeta(ds)}
      </span>
    </Link>
  );
}

export function DatasetsPage() {
  const me = useMe();
  const { workspace } = useWorkspace();
  const writable = canWrite(me, workspace);
  const [query, setQuery] = useState("");
  const listed = useApi<Dataset[]>(
    `/datasets${qs({ workspace: workspace.name })}`,
    {
      select: (list) => [...list].sort((a, b) => a.name.localeCompare(b.name)),
    },
  );
  const datasets = listed.data ?? null;
  const error = listed.error ? String(listed.error) : null;

  const loading = !datasets && !error;
  const visible = (datasets ?? []).filter((d) =>
    query ? d.name.toLowerCase().includes(query.toLowerCase()) : true,
  );

  return (
    <>
      <PageHeader title="Datasets" />
      <div className="page">
        <div className="toolbar mb-3">
          <input
            className="input w-64"
            placeholder="Filter datasets"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <span className="row-meta ml-auto">
            {loading ? "Loading…" : `${visible.length} datasets`}
          </span>
        </div>
        {error && <div className="pane-status error">{error}</div>}
        {loading && (
          <div className="card rows">
            {Array.from({ length: 6 }, (_, i) => (
              <div key={i} className="row grid-cols-[1fr_auto]">
                <span
                  className="skeleton h-4"
                  style={{ width: `${30 + i * 6}%` }}
                />
                <span className="skeleton h-3 w-40" />
              </div>
            ))}
          </div>
        )}
        {!loading && (
          <>
            {datasets && datasets.length === 0 && !writable && (
              <div className="card pane-status">
                No datasets in @{workspace.name}. This workspace is read-only
                for you.
              </div>
            )}
            {datasets && datasets.length === 0 && writable && (
              <div className="card pane-status">
                No datasets in @{workspace.name} — run{" "}
                <span className="font-mono">
                  {workspace.type === "shared"
                    ? `evalkit sync <dir> --workspace ${workspace.name}`
                    : "evalkit run finance --agent <your-agent>"}
                </span>{" "}
                from a repo with an evalkit.yaml.
              </div>
            )}
            {datasets && datasets.length > 0 && visible.length === 0 && (
              <div className="card pane-status">
                No datasets match this filter.
              </div>
            )}
            {visible.length > 0 && (
              <div className="card rows">
                {visible.map((ds) => (
                  <DatasetRow key={ds.ref} ds={ds} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </>
  );
}
