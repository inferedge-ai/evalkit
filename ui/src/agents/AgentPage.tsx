import { Fragment, useEffect, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { AgentForm } from "./AgentForm";
import { HarnessCheckPanel } from "./HarnessCheckPanel";
import { ImportPanel } from "./ImportPanel";
import { MappingsPanel } from "./MappingsPanel";
import { agentKind, agentTabs, isAgentTab } from "../lib/agents";
import type { AgentTab } from "../lib/agents";
import { canUseAgent, canWrite } from "../lib/access";
import { SETTING_FIELDS } from "../lib/agentForm";
import { api, apiJson, isNotFound } from "../lib/api";
import { httpUrl } from "../lib/safeUrl";
import type { AgentInfo } from "../lib/types";
import { CopyText, Tabs } from "../lib/ui";
import { useMe } from "../shell/capabilities";
import { NotFound } from "../shell/NotFound";
import { splitRef } from "../lib/workspace";
import { useFollowWorkspace } from "../shell/workspace";

function Overview({ agent }: { agent: AgentInfo }) {
  const repoHref = httpUrl(agent.repo_url);
  return (
    <dl className="grid max-w-3xl grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-lede">
      <dt className="text-ink-3">Name</dt>
      <dd className="font-mono">{agent.name}</dd>
      <dt className="text-ink-3">Workspace</dt>
      <dd>
        @{agent.workspace}{" "}
        <span className="row-meta">({agent.workspace_type})</span>
      </dd>
      <dt className="text-ink-3">Kind</dt>
      <dd>{agentKind(agent)}</dd>
      <dt className="text-ink-3">Driven by</dt>
      <dd>{DRIVERS[agent.driver]}</dd>
      <dt className="text-ink-3">Harness URL</dt>
      <dd className="font-mono break-all">
        {agent.harness_url ??
          "none (code-driven agent; runs from run_experiment)"}
      </dd>
      <dt className="text-ink-3">Trajectories</dt>
      <dd>
        {agent.trajectory_source === "otel"
          ? "converted from spans the agent exports, with its active mapping"
          : "ATIF served by the harness or returned by the task"}
      </dd>
      {agent.repo_url && (
        <>
          <dt className="text-ink-3">Repository</dt>
          <dd>
            {repoHref ? (
              <a
                className="text-brand"
                href={repoHref}
                target="_blank"
                rel="noopener"
              >
                {agent.repo_url}
              </a>
            ) : (
              agent.repo_url
            )}
          </dd>
        </>
      )}
      {SETTING_FIELDS.map(({ key, label }) => (
        <Fragment key={key}>
          <dt className="text-ink-3">{label}</dt>
          <dd className="num">{agent.settings[key]}</dd>
        </Fragment>
      ))}
      {agent.archived && (
        <>
          <dt className="text-ink-3">State</dt>
          <dd>archived</dd>
        </>
      )}
    </dl>
  );
}

const DRIVERS: Record<AgentInfo["driver"], string> = {
  server: "the server (shared agent)",
  cli: "its owner's CLI (evalkit run)",
  sdk: "the SDK (evalkit.sdk.run_experiment)",
};

export function AgentPage() {
  const { agent: slug = "", tab } = useParams();
  useFollowWorkspace(splitRef(slug).workspace);
  const navigate = useNavigate();
  const me = useMe();
  const [agent, setAgent] = useState<AgentInfo | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    api<AgentInfo>(`/agents/${encodeURIComponent(slug)}`)
      .then(setAgent)
      .catch((e) => {
        if (isNotFound(e)) setAgent(null);
        else setError(e instanceof Error ? e.message : String(e));
      });
  }, [slug]);

  if (!isAgentTab(tab))
    return (
      <Navigate to={`/agents/${encodeURIComponent(slug)}/overview`} replace />
    );
  if (agent === null)
    return (
      <NotFound
        what={`agent ${slug}`}
        up={{ label: "All agents", to: "/agents" }}
      />
    );
  const writable =
    agent &&
    canWrite(me, { name: agent.workspace, type: agent.workspace_type });
  const tabs = agent
    ? agentTabs(agent).filter(
        (t) =>
          (t.id !== "check" || canUseAgent(me, agent)) &&
          (t.id !== "import" || (writable && !agent.archived)),
      )
    : [];
  const archive = async () => {
    if (!agent) return;
    if (
      !window.confirm(
        `Archive ${agent.ref}? Nobody can run it until it's restored. Its runs and results stay.`,
      )
    )
      return;
    setError(null);
    try {
      setAgent(
        await apiJson<AgentInfo>(
          `/agents/${encodeURIComponent(agent.ref)}/archive`,
          "POST",
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  const restore = async () => {
    if (!agent) return;
    setError(null);
    try {
      setAgent(
        await apiJson<AgentInfo>(
          `/agents/${encodeURIComponent(agent.ref)}/restore`,
          "POST",
        ),
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  if (agent && !tabs.some((t) => t.id === tab))
    return (
      <Navigate to={`/agents/${encodeURIComponent(slug)}/overview`} replace />
    );
  return (
    <div className="page">
      <div className="toolbar mb-2">
        <h1 className="max-w-160 truncate font-mono text-head font-semibold">
          <CopyText value={slug} />
        </h1>
        {agent && <span className="chip muted">{agentKind(agent)}</span>}
        {writable && !agent.archived && (
          <>
            <button
              className="btn btn-sm ml-auto"
              onClick={() => setEditing(true)}
            >
              Edit
            </button>
            <button className="btn btn-sm" onClick={archive}>
              Archive
            </button>
          </>
        )}
        {writable && agent.archived && (
          <button className="btn btn-sm ml-auto" onClick={restore}>
            Restore
          </button>
        )}
        <Link
          className={`btn btn-quiet text-brand ${writable ? "" : "ml-auto"}`}
          to={`/runs?agent=${encodeURIComponent(slug)}`}
        >
          Runs ↗
        </Link>
        <Link className="btn btn-quiet text-brand" to="/agents">
          All agents ↗
        </Link>
      </div>
      {error && <div className="pane-status error">{error}</div>}
      {agent && (
        <>
          <Tabs<AgentTab>
            tabs={tabs}
            active={tab}
            onSelect={(next) =>
              navigate(`/agents/${encodeURIComponent(slug)}/${next}`)
            }
          />
          <div className="mt-4">
            {tab === "overview" && <Overview agent={agent} />}
            {tab === "mappings" && <MappingsPanel agent={slug} />}
            {tab === "import" && <ImportPanel agent={slug} />}
            {tab === "check" && <HarnessCheckPanel agent={agent.ref} />}
          </div>
          {editing && (
            <AgentForm
              workspace={agent.workspace}
              shared={agent.workspace_type === "shared"}
              agent={agent}
              onSaved={(saved) => {
                setAgent(saved);
                setEditing(false);
              }}
              onClose={() => setEditing(false)}
            />
          )}
        </>
      )}
    </div>
  );
}
