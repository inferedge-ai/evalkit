import { useEffect, useMemo, useState } from "react";
import { ArrowUpRight } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import type { ColDef } from "ag-grid-community";
import { qs } from "../lib/api";
import {
  ColumnsMenu,
  DataGrid,
  compareNullsLast,
  readValue,
  renderCell,
  useDataGrid,
} from "../lib/DataGrid";
import { fmtWhen } from "../lib/format";
import { useApi } from "../lib/query";
import type { AgentInfo, RunList, RunListItem } from "../lib/types";
import { Select } from "../lib/Select";
import { splitRef } from "../lib/workspace";
import { useFollowWorkspace, useWorkspace } from "../shell/workspace";
import { PageHeader } from "@/components/widgets/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";

const NO_RUNS: RunListItem[] = [];

const getRunId = (run: RunListItem) => run.run_id;

function runColumns(agent: string): ColDef<RunListItem>[] {
  const href = (run: RunListItem) =>
    `/runs/${encodeURIComponent(agent)}/${encodeURIComponent(run.run_id)}`;
  return [
    {
      colId: "title",
      headerName: "title",
      type: "text",
      initialFlex: 2,
      valueGetter: readValue((run) => run.title ?? run.run_id),
      cellRenderer: renderCell<RunListItem>((run) => (
        <Link
          className="font-medium text-brand hover:underline"
          to={href(run)}
          title={run.title || run.run_id}
        >
          {run.title || <span className="font-mono">{run.run_id}</span>}
        </Link>
      )),
    },
    {
      colId: "run_id",
      headerName: "run id",
      type: "text",
      field: "run_id",
      cellRenderer: renderCell<RunListItem>((run) => (
        <span className="row-meta font-mono text-ink-4">{run.run_id}</span>
      )),
    },
    {
      colId: "experiment",
      headerName: "experiment",
      type: "number",
      initialWidth: 110,
      valueGetter: readValue((run) => run.experiment_number),
      comparator: compareNullsLast,
      cellRenderer: renderCell<RunListItem>((run) => (
        <span className="row-meta font-mono">
          {run.experiment_id != null ? `#${run.experiment_number ?? "?"}` : ""}
        </span>
      )),
    },
    {
      colId: "case",
      headerName: "case",
      type: "text",
      valueGetter: readValue((run) => run.case_id ?? ""),
      cellRenderer: renderCell<RunListItem>((run) => (
        <span className="row-meta font-mono">{run.case_id}</span>
      )),
    },
    {
      colId: "user",
      headerName: "user",
      type: "text",
      valueGetter: readValue((run) => run.user_id ?? ""),
      cellRenderer: renderCell<RunListItem>((run) => (
        <span className="row-meta font-mono">{run.user_id}</span>
      )),
    },
    {
      colId: "created",
      headerName: "created",
      type: "rightAligned",
      initialFlex: 0,
      initialWidth: 140,
      valueGetter: readValue((run) => run.created_at ?? ""),
      cellRenderer: renderCell<RunListItem>((run) => (
        <span className="row-meta">{fmtWhen(run.created_at) ?? ""}</span>
      )),
    },
  ];
}

export function RunsBrowser() {
  const { workspace, workspaces } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const agent = params.get("agent") ?? "";
  const agentWorkspace = splitRef(agent).workspace;
  useFollowWorkspace(agentWorkspace);
  const [query, setQuery] = useState("");
  const [userId, setUserId] = useState("");
  const [noSession, setNoSession] = useState(false);
  const following =
    agentWorkspace !== workspace.name &&
    workspaces.some((w) => w.name === agentWorkspace);
  const listedAgents = useApi<AgentInfo[]>(
    following ? null : `/agents${qs({ workspace: workspace.name })}`,
    {
      select: (list) => [...list].sort((a, b) => a.name.localeCompare(b.name)),
    },
  );
  const agents = listedAgents.data ?? [];
  const listedRuns = useApi<RunList>(
    agent
      ? `/agents/${encodeURIComponent(agent)}/runs${qs({
          query: query || undefined,
          user_id: userId || undefined,
          no_session: noSession ? "true" : undefined,
        })}`
      : null,
  );
  const runs = listedRuns.data ?? null;
  const loadError = listedAgents.error ?? listedRuns.error;
  const error = loadError ? String(loadError) : null;
  const columns = useMemo(() => runColumns(agent), [agent]);
  const grid = useDataGrid<RunListItem>("runs");
  const otel =
    agents.find((a) => a.ref === agent)?.trajectory_source === "otel";

  // Decide on this visit's list, not a cached one that may predate a new agent.
  const settled = listedAgents.isFetchedAfterMount && !listedAgents.isFetching;
  useEffect(() => {
    const ordered = listedAgents.data;
    if (settled && ordered && !ordered.some((a) => a.ref === agent))
      setParams(ordered.length ? { agent: ordered[0].ref } : {}, {
        replace: true,
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settled, listedAgents.data]);

  return (
    <>
      <PageHeader title="Runs" />
      <div className="page">
        <div className="toolbar mb-3">
          <Select
            ariaLabel="agent"
            value={agent}
            onChange={(a) => setParams({ agent: a })}
            placeholder="No agents"
            options={agents.map((a) => ({ value: a.ref, label: a.name }))}
          />
          <Input
            className="w-72"
            placeholder={
              otel ? "Session id prefix" : "Search id prefix or title"
            }
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          {!otel && (
            <Input
              className="w-44"
              placeholder="User id"
              value={userId}
              onChange={(e) => setUserId(e.target.value)}
            />
          )}
          {otel && (
            <label className="flex cursor-pointer items-center gap-1.5 text-meta text-muted-foreground select-none">
              <Checkbox checked={noSession} onCheckedChange={setNoSession} />
              no session
            </label>
          )}
          {otel && (
            <Link
              className={buttonVariants({ variant: "link" })}
              to={`/agents/${encodeURIComponent(agent)}/mappings`}
            >
              Agent <ArrowUpRight size={12} />
            </Link>
          )}
          <span className="row-meta num ml-auto">
            {runs
              ? grid.filteredCount !== null
                ? `${grid.filteredCount} of the ${runs.runs.length} loaded`
                : `${runs.total} runs`
              : ""}
          </span>
          {agent && !error && <ColumnsMenu grid={grid} />}
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
          <DataGrid
            grid={grid}
            rows={runs?.runs ?? NO_RUNS}
            columns={columns}
            getRowId={getRunId}
            loading={!runs}
            empty="No runs match."
          />
        )}
      </div>
    </>
  );
}
