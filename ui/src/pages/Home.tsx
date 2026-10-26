import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, qs } from "../lib/api";
import type { AgentInfo, Dataset } from "../lib/types";
import { useWorkspace } from "../shell/workspace";

function SkeletonRows({ n }: { n: number }) {
  return (
    <div className="rows">
      {Array.from({ length: n }, (_, i) => (
        <div key={i} className="row grid-cols-[1fr_auto]">
          <span
            className="skeleton h-4"
            style={{ width: `${40 + ((i * 13) % 45)}%` }}
          />
          <span className="skeleton h-3 w-28" />
        </div>
      ))}
    </div>
  );
}

function DatasetRow({ ds }: { ds: Dataset }) {
  return (
    <Link
      to={`/datasets/${encodeURIComponent(ds.ref)}`}
      className="row grid-cols-[minmax(0,1fr)_auto]"
    >
      <span className="row-strong">{ds.name}</span>
      <span className="chip muted">{ds.kind}</span>
    </Link>
  );
}

function AgentRow({ agent }: { agent: AgentInfo }) {
  return (
    <Link
      to={`/agents/${encodeURIComponent(agent.ref)}/overview`}
      className="row grid-cols-[minmax(0,1fr)_auto]"
    >
      <span className="row-strong">{agent.name}</span>
      <span className="row-meta truncate">
        {agent.harness_url ?? "workspace agent · runs from its owner's CLI"}
      </span>
    </Link>
  );
}

export function Home() {
  const { workspace } = useWorkspace();
  const [datasets, setDatasets] = useState<Dataset[] | null>(null);
  const [datasetsError, setDatasetsError] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  const [agentsError, setAgentsError] = useState<string | null>(null);

  useEffect(() => {
    let stale = false;
    const scope = qs({ workspace: workspace.name });
    const message = (e: unknown) =>
      e instanceof Error ? e.message : String(e);
    setDatasets(null);
    setAgents(null);
    setDatasetsError(null);
    setAgentsError(null);
    api<Dataset[]>(`/datasets${scope}`)
      .then((list) => {
        if (!stale)
          setDatasets([...list].sort((a, b) => a.name.localeCompare(b.name)));
      })
      .catch((e) => !stale && setDatasetsError(message(e)));
    api<AgentInfo[]>(`/agents${scope}`)
      .then((list) => {
        if (!stale)
          setAgents([...list].sort((a, b) => a.name.localeCompare(b.name)));
      })
      .catch((e) => !stale && setAgentsError(message(e)));
    return () => {
      stale = true;
    };
  }, [workspace.name]);

  const loading = (!datasets && !datasetsError) || (!agents && !agentsError);
  const empty = !datasets?.length && !agents?.length;

  return (
    <div className="page">
      <div className="toolbar mb-3">
        <h1 className="page-title">@{workspace.name}</h1>
        <span className="chip muted">{workspace.type}</span>
        <Link to="/datasets" className="btn btn-quiet ml-auto text-brand">
          All datasets →
        </Link>
        <Link to="/agents" className="btn btn-quiet text-brand">
          All agents →
        </Link>
      </div>
      {datasetsError && (
        <div className="pane-status error">{datasetsError}</div>
      )}
      {agentsError && <div className="pane-status error">{agentsError}</div>}
      {loading && (
        <div className="card">
          <SkeletonRows n={8} />
        </div>
      )}
      {!loading && (
        <div className="grid gap-x-5 gap-y-2 lg:grid-cols-2">
          {!!datasets?.length && (
            <section className="min-w-0">
              <div className="eyebrow">Datasets</div>
              <div className="card rows">
                {datasets.map((ds) => (
                  <DatasetRow key={ds.ref} ds={ds} />
                ))}
              </div>
            </section>
          )}
          {!!agents?.length && (
            <section className="min-w-0">
              <div className="eyebrow">Agents</div>
              <div className="card rows">
                {agents.map((a) => (
                  <AgentRow key={a.ref} agent={a} />
                ))}
              </div>
            </section>
          )}
          {empty && (
            <div className="card pane-status lg:col-span-2">
              {workspace.type === "shared" ? (
                <>
                  No datasets or agents yet — run{" "}
                  <span className="font-mono">
                    evalkit sync &lt;dir&gt; --workspace {workspace.name}
                  </span>{" "}
                  or add an agent.
                </>
              ) : (
                <>
                  Nothing here yet — run{" "}
                  <span className="font-mono">
                    evalkit run &lt;dataset&gt; --agent &lt;agent&gt;
                  </span>{" "}
                  from a repo with an evalkit.yaml.
                </>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
