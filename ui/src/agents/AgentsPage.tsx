import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AgentForm } from "./AgentForm";
import { canWrite } from "../lib/access";
import { agentKind } from "../lib/agents";
import { api, qs } from "../lib/api";
import type { AgentInfo } from "../lib/types";
import { useMe } from "../shell/capabilities";
import { useWorkspace } from "../shell/workspace";

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
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  const [archived, setArchived] = useState<AgentInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const navigate = useNavigate();

  const { workspace } = useWorkspace();
  const writable = canWrite(me, workspace) && !workspace.archived;

  useEffect(() => {
    let stale = false;
    setAgents(null);
    setArchived([]);
    setError(null);
    api<AgentInfo[]>(
      `/agents${qs({ workspace: workspace.name, include_archived: "true" })}`,
    )
      .then((list) => {
        if (stale) return;
        const sorted = [...list].sort((a, b) => a.name.localeCompare(b.name));
        setAgents(sorted.filter((a) => !a.archived));
        setArchived(sorted.filter((a) => a.archived));
      })
      .catch((e) => !stale && setError(String(e)));
    return () => {
      stale = true;
    };
  }, [workspace.name]);

  return (
    <div className="page">
      <div className="toolbar mb-3">
        <h1 className="page-title">Agents</h1>
        {agents && (
          <span className="row-meta num ml-auto">{agents.length} agents</span>
        )}
        {writable && (
          <button className="btn btn-sm" onClick={() => setCreating(true)}>
            New agent
          </button>
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
          onSaved={(agent) =>
            navigate(`/agents/${encodeURIComponent(agent.ref)}/overview`)
          }
          onClose={() => setCreating(false)}
        />
      )}
    </div>
  );
}
