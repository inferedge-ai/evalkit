import { Badge } from "@/components/ui/badge";
import type { ColDef } from "ag-grid-community";
import {
  STATUS_TONE,
  isIncomplete,
  progressLabel,
  shortProgress,
} from "../lib/experimentStatus";
import {
  elapsedMs,
  fmtWhen,
  formatElapsed,
  formatMs,
  selectionLabel,
} from "../lib/format";
import { compareNullsLast, readValue, renderCell } from "../lib/DataGrid";
import { describeTerm } from "../lib/glossary";
import { agentCommitUrl } from "../lib/meta";
import { splitRef } from "../lib/workspace";
import { scoreValue, type BucketColumn } from "../lib/bucketColumns";
import type { Experiment, ExperimentListItem } from "../lib/types";
import {
  CostCell,
  RevisionChip,
  ScoreCell,
  UserPill,
  WorkspaceChip,
} from "./bits";

export function agentTag(exp: Experiment): string {
  const model = exp.config.model
    ? ` · ${exp.config.model.split("/").pop()}`
    : "";
  return `${exp.agent}${model}`;
}

export function agentRevision(exp: Experiment): {
  sha?: string;
  branch?: string;
} {
  const caps = exp.config.snapshot.capabilities;
  return {
    sha: typeof caps.revision === "string" ? caps.revision : undefined,
    branch: typeof caps.branch === "string" ? caps.branch : undefined,
  };
}

type ExperimentColumn = ColDef<ExperimentListItem>;

export function experimentTailColumns(
  columns: BucketColumn[],
): ExperimentColumn[] {
  return [
    ...columns.map((column): ExperimentColumn => ({
      colId: `score:${column.bucket}`,
      headerName: column.short,
      headerTooltip: describeTerm(`bucket:${column.bucket}`) ?? column.label,
      type: "number",
      valueGetter: readValue((exp) => scoreValue(exp.scores, column.bucket)),
      comparator: compareNullsLast,
      cellRenderer: renderCell<ExperimentListItem>((exp) => (
        <ScoreCell score={scoreValue(exp.scores, column.bucket)} />
      )),
    })),
    {
      colId: "cost",
      headerName: "cost",
      headerTooltip: "mean agent cost per run (gauge_agent_cost_usd)",
      type: "number",
      valueGetter: readValue((exp) => exp.cost_usd),
      comparator: compareNullsLast,
      cellRenderer: renderCell<ExperimentListItem>((exp) => (
        <CostCell cost={exp.cost_usd} />
      )),
    },
    {
      colId: "latency",
      headerName: "latency",
      initialHide: true,
      type: "number",
      valueGetter: readValue((exp) => exp.avg_latency_ms),
      comparator: compareNullsLast,
      cellRenderer: renderCell<ExperimentListItem>((exp) => (
        <span className="row-meta num">
          {exp.avg_latency_ms ? formatMs(exp.avg_latency_ms) : ""}
        </span>
      )),
    },
    {
      colId: "status",
      headerName: "status",
      type: "text",
      field: "status",
      cellRenderer: renderCell<ExperimentListItem>((exp) => (
        <Badge variant={STATUS_TONE[exp.status] ?? "secondary"}>
          {exp.status}
        </Badge>
      )),
    },
    {
      colId: "cases",
      headerName: "cases",
      headerTooltip: "cases evaluated / pinned",
      type: "number",
      valueGetter: readValue((exp) => exp.progress.evaluated),
      cellRenderer: renderCell<ExperimentListItem>((exp) => (
        <span
          className={`row-meta num ${exp.progress.errored || exp.progress.partly_errored ? "text-err-text" : isIncomplete(exp.progress) ? "text-warn" : ""}`}
          title={progressLabel(exp.progress)}
        >
          {shortProgress(exp.progress)}
        </span>
      )),
    },
    {
      colId: "agent",
      headerName: "agent",
      type: "text",
      valueGetter: readValue((exp) => agentTag(exp)),
      cellRenderer: renderCell<ExperimentListItem>((exp) => (
        <span className="inline-flex items-center gap-1" title={agentTag(exp)}>
          {splitRef(exp.agent).name}
          <WorkspaceChip workspace={exp.workspace} />
          {exp.config.model && (
            <span className="row-meta">
              {exp.config.model.split("/").pop()}
            </span>
          )}
        </span>
      )),
    },
    {
      colId: "judge",
      headerName: "judge",
      headerTooltip: "LLM judge model and reasoning effort",
      initialHide: true,
      type: "text",
      valueGetter: readValue((exp) => exp.config.judge?.model ?? ""),
      cellRenderer: renderCell<ExperimentListItem>(({ config: { judge } }) =>
        judge?.model ? (
          <span className="inline-flex items-center gap-1" title={judge.model}>
            <span className="row-meta">{judge.model.split("/").pop()}</span>
            {judge.reasoning_effort && (
              <span className="row-meta text-ink-4">
                {judge.reasoning_effort}
              </span>
            )}
          </span>
        ) : null,
      ),
    },
    {
      colId: "selection",
      headerName: "selection",
      headerTooltip: "the cases this experiment ran on",
      type: "text",
      valueGetter: readValue(
        (exp) => selectionLabel({ selection: exp.config.selection }) ?? "",
      ),
      cellRenderer: renderCell<ExperimentListItem>((exp) => {
        const label = selectionLabel({ selection: exp.config.selection });
        return (
          <span className="row-meta" title={label ?? undefined}>
            {label}
          </span>
        );
      }),
    },
    {
      colId: "rev",
      headerName: "rev",
      headerTooltip: "harness git revision, and the cases revision",
      initialHide: true,
      valueGetter: readValue((exp) => agentRevision(exp).sha ?? ""),
      cellRenderer: renderCell<ExperimentListItem>((exp) => {
        const rev = agentRevision(exp);
        const cases = exp.config.revision;
        return (
          <span className="inline-flex items-center gap-2">
            <RevisionChip
              sha={rev.sha}
              branch={rev.branch}
              url={agentCommitUrl(exp.agent, rev.sha)}
              compact
            />
            {cases?.sha && (
              <span
                className="num font-mono text-meta text-ink-4"
                title={`cases ${cases.branch ? `${cases.branch} @ ` : ""}${cases.sha}${cases.dirty ? " (dirty)" : ""}`}
              >
                cases {cases.sha.slice(0, 8)}
                {cases.dirty ? "*" : ""}
              </span>
            )}
          </span>
        );
      }),
    },
    {
      colId: "user",
      headerName: "user",
      type: "text",
      field: "created_by",
      cellRenderer: renderCell<ExperimentListItem>((exp) => (
        <UserPill user={exp.created_by} />
      )),
    },
    {
      colId: "took",
      headerName: "took",
      headerTooltip: "wall time from start to end",
      initialHide: true,
      type: "number",
      valueGetter: readValue((exp) => elapsedMs(exp.started_at, exp.ended_at)),
      comparator: compareNullsLast,
      cellRenderer: renderCell<ExperimentListItem>((exp) => {
        const took = elapsedMs(exp.started_at, exp.ended_at);
        return (
          <span className="row-meta num">
            {took !== null ? formatElapsed(took) : ""}
          </span>
        );
      }),
    },
    {
      colId: "started",
      headerName: "started",
      type: "rightAligned",
      initialFlex: 0,
      initialWidth: 140,
      field: "started_at",
      cellRenderer: renderCell<ExperimentListItem>((exp) => (
        <span className="row-meta" title={exp.started_at ?? undefined}>
          {fmtWhen(exp.started_at) ?? ""}
          {exp.repetitions > 1 ? ` ×${exp.repetitions}` : ""}
        </span>
      )),
    },
  ];
}
