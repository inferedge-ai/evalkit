import { useState } from "react";
import { Bot } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";
import { AgentForm } from "./AgentForm";
import { canWrite } from "../lib/access";
import { agentKind } from "../lib/agents";
import { qs } from "../lib/api";
import { useApi } from "../lib/query";
import type { AgentInfo } from "../lib/types";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const ROW_COLUMNS = "grid-cols-[minmax(0,1fr)_auto_minmax(0,2fr)_auto]";

function AgentRow({ agent }: { agent: AgentInfo }) {
  return (
    <Link
      className={`row ${ROW_COLUMNS}`}
      to={`/agents/${encodeURIComponent(agent.ref)}/overview`}
    >
      <span className="row-strong">{agent.name}</span>
      <Badge variant="secondary">{agentKind(agent)}</Badge>
      <span className="row-meta truncate font-mono">
        {agent.harness_url ?? ""}
      </span>
      <Badge variant="secondary">{agent.driver}</Badge>
    </Link>
  );
}

export function AgentsPage() {
  const me = useMe();
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  const { workspace } = useWorkspace();
  const writable = canWrite(me, workspace) && !workspace.archived;

  const listed = useApi<AgentInfo[]>(
    `/agents${qs({ workspace: workspace.name, include_archived: "true" })}`,
    {
      select: (list) => [...list].sort((a, b) => a.name.localeCompare(b.name)),
    },
  );
  const agents = listed.data?.filter((a) => !a.archived) ?? null;
  const archived = listed.data?.filter((a) => a.archived) ?? [];
  const error = listed.error ? String(listed.error) : null;
  const loading = !agents && !error;

  return (
    <>
      <PageHeader
        title="Agents"
        description={
          agents
            ? `${agents.length} agent${agents.length === 1 ? "" : "s"}`
            : undefined
        }
        actions={
          writable && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCreating(true)}
            >
              New agent
            </Button>
          )
        }
      />
      <div className="page">
        {error && <div className="pane-status error">{error}</div>}
        {loading && (
          <Card className="rows gap-0 py-0">
            {Array.from({ length: 4 }, (_, i) => (
              <div key={i} className={`row ${ROW_COLUMNS}`}>
                <Skeleton className="h-4" style={{ width: `${45 + i * 9}%` }} />
                <Skeleton className="h-4 w-32" />
                <Skeleton className="h-3 w-48 max-w-full" />
                <Skeleton className="h-4 w-12" />
              </div>
            ))}
          </Card>
        )}
        {agents && agents.length === 0 && (
          <Card className="py-8">
            <Empty
              icon={Bot}
              title={`No agents in @${workspace.name}`}
              description={
                writable ? (
                  <>
                    Create one with New agent, or run{" "}
                    <span className="font-mono">evalkit agents create</span>.
                  </>
                ) : (
                  "This workspace is read-only for you."
                )
              }
            />
          </Card>
        )}
        {agents && agents.length > 0 && (
          <Card className="rows gap-0 py-0">
            {agents.map((agent) => (
              <AgentRow key={agent.ref} agent={agent} />
            ))}
          </Card>
        )}
        {archived.length > 0 && (
          <details className="mt-4">
            <summary className="row-meta cursor-pointer">
              Archived ({archived.length})
            </summary>
            <Card className="rows gap-0 py-0 mt-2">
              {archived.map((agent) => (
                <AgentRow key={agent.ref} agent={agent} />
              ))}
            </Card>
          </details>
        )}
        {creating && (
          <AgentForm
            workspace={workspace.name}
            shared={workspace.type === "shared"}
            onSaved={(agent) =>
              navigate(`/agents/${encodeURIComponent(agent.ref)}/overview`)
            }
            onClose={() => setCreating(false)}
          />
        )}
      </div>
    </>
  );
}
