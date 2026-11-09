import { ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import { PageHeader } from "@/components/widgets/PageHeader";
import { qs } from "../lib/api";
import { useApi } from "../lib/query";
import type { AgentInfo, Dataset } from "../lib/types";
import { useWorkspace } from "../shell/workspace";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";

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
      <Badge variant="secondary">{ds.kind}</Badge>
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

function sortByName<T extends { name: string }>(list: T[]): T[] {
  return [...list].sort((a, b) => a.name.localeCompare(b.name));
}

export function Home() {
  const { workspace } = useWorkspace();
  const scope = qs({ workspace: workspace.name });
  const datasetsQuery = useApi<Dataset[]>(`/datasets${scope}`, {
    select: sortByName,
  });
  const agentsQuery = useApi<AgentInfo[]>(`/agents${scope}`, {
    select: sortByName,
  });
  const datasets = datasetsQuery.data ?? null;
  const agents = agentsQuery.data ?? null;
  const datasetsError = datasetsQuery.error?.message ?? null;
  const agentsError = agentsQuery.error?.message ?? null;

  const loading = (!datasets && !datasetsError) || (!agents && !agentsError);
  const empty = !datasets?.length && !agents?.length;

  return (
    <>
      <PageHeader
        title={
          <>
            @{workspace.name}{" "}
            <Badge variant="secondary" className="align-middle font-normal">
              {workspace.type}
            </Badge>
          </>
        }
        actions={
          <>
            <Link
              to="/datasets"
              className={cn(buttonVariants({ variant: "ghost" }), "text-brand")}
            >
              All datasets <ArrowRight size={14} />
            </Link>
            <Link
              to="/agents"
              className={cn(buttonVariants({ variant: "ghost" }), "text-brand")}
            >
              All agents <ArrowRight size={14} />
            </Link>
          </>
        }
      />
      <div className="page">
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
    </>
  );
}
