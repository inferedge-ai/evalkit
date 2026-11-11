import type { ReactNode } from "react";
import { Skeleton } from "@/components/ui/skeleton";
import { ArrowRight, Database } from "lucide-react";
import { Link } from "react-router-dom";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { qs } from "../lib/api";
import { useApi } from "../lib/query";
import type { AgentInfo, Dataset } from "../lib/types";
import { useWorkspace } from "../shell/workspace";
import { buttonVariants } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="min-w-0">
      <div className="eyebrow">{title}</div>
      <Card className="rows gap-0 py-0">{children}</Card>
    </section>
  );
}

function SkeletonRows() {
  return Array.from({ length: 3 }, (_, i) => (
    <div key={i} className="row grid-cols-[1fr_auto]">
      <Skeleton className="h-4" style={{ width: `${40 + ((i * 13) % 45)}%` }} />
      <Skeleton className="h-3 w-28" />
    </div>
  ));
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
              className={buttonVariants({ variant: "link" })}
            >
              All datasets <ArrowRight size={14} />
            </Link>
            <Link to="/agents" className={buttonVariants({ variant: "link" })}>
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
        <div className="grid gap-4 lg:grid-cols-2">
          {loading && (
            <>
              <Section title="Datasets">
                <SkeletonRows />
              </Section>
              <Section title="Agents">
                <SkeletonRows />
              </Section>
            </>
          )}
          {!loading && !!datasets?.length && (
            <Section title="Datasets">
              {datasets.map((ds) => (
                <DatasetRow key={ds.ref} ds={ds} />
              ))}
            </Section>
          )}
          {!loading && !!agents?.length && (
            <Section title="Agents">
              {agents.map((a) => (
                <AgentRow key={a.ref} agent={a} />
              ))}
            </Section>
          )}
          {!loading && !datasetsError && !agentsError && empty && (
            <Card className="py-8 lg:col-span-2">
              {workspace.type === "shared" ? (
                <Empty
                  icon={Database}
                  title={`No datasets or agents in @${workspace.name} yet`}
                  description={
                    <>
                      Run{" "}
                      <span className="font-mono">
                        evalkit sync &lt;dir&gt; --workspace {workspace.name}
                      </span>{" "}
                      or add an agent.
                    </>
                  }
                />
              ) : (
                <Empty
                  icon={Database}
                  title="Nothing here yet"
                  description={
                    <>
                      Run{" "}
                      <span className="font-mono">
                        evalkit run &lt;dataset&gt; --agent &lt;agent&gt;
                      </span>{" "}
                      from a repo with an evalkit.yaml.
                    </>
                  }
                />
              )}
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
