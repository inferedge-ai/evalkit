import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AgentForm } from "./AgentForm";
import { canWrite } from "../lib/access";
import { agentKind } from "../lib/agents";
import { qs } from "../lib/api";
import { useApi } from "../lib/query";
import type { AgentInfo } from "../lib/types";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Button } from "@/components/ui/button";

function AgentRow({ agent }: { agent: AgentInfo }) {
  return (
    <Link
      className="row grid-cols-[minmax(0,1fr)_auto_minmax(0,2fr)_auto]"
      to={`/agents/${encodeURIComponent(agent.ref)}/overview`}
    >
      <span className="row-strong">{agent.name}</span>
      <span className="chip muted">{agentKind(agent)}</span>
      <span className="row-meta truncate font-mono">
        {agent.harness_url ?? ""}
      </span>
      <span className="chip muted">{agent.driver}</span>
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

  return (
    <>
      <PageHeader
        title="Agents"
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
        <div className="toolbar mb-3">
          {agents && (
            <span className="row-meta num ml-auto">{agents.length} agents</span>
          )}
        </div>
        {error && <div className="pane-status error">{error}</div>}
        {agents && agents.length === 0 && (
          <div className="pane-status">
            No agents in @{workspace.name}.
            {writable && (
              <>
                {" "}
                Create one here or with{" "}
                <span className="font-mono">evalkit agents create</span>.
              </>
            )}
          </div>
        )}
        {agents && agents.length > 0 && (
          <div className="card rows">
            {agents.map((agent) => (
              <AgentRow key={agent.ref} agent={agent} />
            ))}
          </div>
        )}
        {archived.length > 0 && (
          <details className="mt-4">
            <summary className="row-meta cursor-pointer">
              Archived ({archived.length})
            </summary>
            <div className="card rows mt-2">
              {archived.map((agent) => (
                <AgentRow key={agent.ref} agent={agent} />
              ))}
            </div>
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
