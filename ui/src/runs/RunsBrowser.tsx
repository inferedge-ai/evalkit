import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, qs } from "../lib/api";
import { DataTable, useDataTable } from "../lib/DataTable";
import type { Column } from "../lib/DataTable";
import { fmtWhen } from "../lib/format";
import type { AgentInfo, RunList, RunListItem } from "../lib/types";
import { Select } from "../lib/Select";
import { splitRef } from "../lib/workspace";
import { useFollowWorkspace, useWorkspace } from "../shell/workspace";

const NO_RUNS: RunListItem[] = [];

function runColumns(agent: string): Column<RunListItem>[] {
  const href = (run: RunListItem) =>
    `/runs/${encodeURIComponent(agent)}/${encodeURIComponent(run.run_id)}`;
  return [
    {
      id: "title",
      header: "title",
      accessorFn: (run) => run.title ?? run.run_id,
      meta: { wrap: true },
      cell: (c) => (
        <Link
          className="font-medium text-brand hover:underline"
          to={href(c.row.original)}
        >
          {c.row.original.title || (
            <span className="font-mono">{c.row.original.run_id}</span>
          )}
        </Link>
      ),
    },
    {
      id: "run_id",
      header: "run id",
      accessorKey: "run_id",
      cell: (c) => (
        <span className="row-meta font-mono text-ink-4">
          {c.row.original.run_id}
        </span>
      ),
    },
    {
      id: "experiment",
      header: "experiment",
      accessorFn: (run) => run.experiment_number ?? undefined,
      sortUndefined: "last",
      meta: { align: "right" },
      cell: (c) => (
        <span className="row-meta font-mono">
          {c.row.original.experiment_id != null
            ? `#${c.row.original.experiment_number ?? "?"}`
            : ""}
        </span>
      ),
    },
    {
      id: "case",
      header: "case",
      accessorFn: (run) => run.case_id ?? "",
      cell: (c) => (
        <span className="row-meta font-mono">{c.row.original.case_id}</span>
      ),
    },
    {
      id: "user",
      header: "user",
      accessorFn: (run) => run.user_id ?? "",
      cell: (c) => (
        <span className="row-meta font-mono">{c.row.original.user_id}</span>
      ),
    },
    {
      id: "created",
      header: "created",
      accessorFn: (run) => run.created_at ?? "",
      meta: { align: "right" },
      cell: (c) => (
        <span className="row-meta">
          {fmtWhen(c.row.original.created_at) ?? ""}
        </span>
      ),
    },
  ];
}

export function RunsBrowser() {
  const { workspace, workspaces } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const agent = params.get("agent") ?? "";
  const agentWorkspace = splitRef(agent).workspace;
  useFollowWorkspace(agentWorkspace);
  const [query, setQuery] = useState("");
  const [userId, setUserId] = useState("");
  const [runs, setRuns] = useState<RunList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noSession, setNoSession] = useState(false);
  const columns = useMemo(() => runColumns(agent), [agent]);
  const table = useDataTable({
    id: "runs",
    data: runs?.runs ?? NO_RUNS,
    columns,
    getRowId: (run) => run.run_id,
  });
  const otel =
    agents.find((a) => a.ref === agent)?.trajectory_source === "otel";

  useEffect(() => {
    if (
      agentWorkspace !== workspace.name &&
      workspaces.some((w) => w.name === agentWorkspace)
    )
      return;
    let stale = false;
    setError(null);
    api<AgentInfo[]>(`/agents${qs({ workspace: workspace.name })}`)
      .then((list) => {
        if (stale) return;
        const ordered = [...list].sort((a, b) => a.name.localeCompare(b.name));
        setAgents(ordered);
        if (!ordered.some((a) => a.ref === agent))
          setParams(ordered.length ? { agent: ordered[0].ref } : {}, {
            replace: true,
          });
      })
      .catch((e) => !stale && setError(String(e)));
    return () => {
      stale = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.name]);

  useEffect(() => {
    let stale = false;
    setRuns(null);
    setError(null);
    if (!agent) return;
    api<RunList>(
      `/agents/${encodeURIComponent(agent)}/runs${qs({
        query: query || undefined,
        user_id: userId || undefined,
        no_session: noSession ? "true" : undefined,
      })}`,
    )
      .then((list) => !stale && setRuns(list))
      .catch((e) => !stale && setError(String(e)));
    return () => {
      stale = true;
    };
  }, [agent, query, userId, noSession]);

  return (
    <div className="page">
      <div className="toolbar mb-3">
        <h1 className="page-title">Runs</h1>
        <Select
          ariaLabel="agent"
          value={agent}
          onChange={(a) => setParams({ agent: a })}
          placeholder="No agents"
          options={agents.map((a) => ({ value: a.ref, label: a.name }))}
        />
        <input
          className="input w-72"
          placeholder={otel ? "Session id prefix" : "Search id prefix or title"}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {!otel && (
          <input
            className="input w-44"
            placeholder="User id"
            value={userId}
            onChange={(e) => setUserId(e.target.value)}
          />
        )}
        {otel && (
          <label className="flex items-center gap-1 text-meta text-ink-2">
            <input
              type="checkbox"
              checked={noSession}
              onChange={(e) => setNoSession(e.target.checked)}
            />
            no session
          </label>
        )}
        {otel && (
          <Link
            className="btn btn-quiet text-brand"
            to={`/agents/${encodeURIComponent(agent)}/mappings`}
          >
            Agent ↗
          </Link>
        )}
        {runs && (
          <span className="row-meta num ml-auto">{runs.total} runs</span>
        )}
      </div>
      {error && (
        <div className="pane-status error">
          {error}
          {!otel && " — the harness may not implement run listing."}
        </div>
      )}
      {runs?.harness_note && (
        <div className="pane-status">
          {runs.harness_note} — showing the runs evalkit stored.
        </div>
      )}
      {agent && !error && (
        <DataTable table={table} loading={!runs} empty="No runs match." />
      )}
    </div>
  );
}
