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
  splitLabel,
} from "../lib/format";
import { columnHelper } from "../lib/DataTable";
import type { Column } from "../lib/DataTable";
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

const helper = columnHelper<ExperimentListItem>();

export function experimentTailColumns(
  columns: BucketColumn[],
): Column<ExperimentListItem>[] {
  return [
    ...columns.map((column) =>
      helper.accessor(
        (exp) => scoreValue(exp.scores, column.bucket) ?? undefined,
        {
          id: `score:${column.bucket}`,
          header: column.short,
          meta: {
            align: "right",
            title: describeTerm(`bucket:${column.bucket}`) ?? column.label,
          },
          sortUndefined: "last",
          cell: (c) => <ScoreCell score={c.getValue()} />,
        },
      ),
    ),
    helper.accessor((exp) => exp.cost_usd ?? undefined, {
      id: "cost",
      sortUndefined: "last",
      header: "cost",
      meta: {
        align: "right",
        title: "mean agent cost per run (gauge_agent_cost_usd)",
      },
      cell: (c) => <CostCell cost={c.getValue()} />,
    }),
    helper.accessor((exp) => exp.avg_latency_ms ?? undefined, {
      id: "latency",
      sortUndefined: "last",
      header: "latency",
      meta: { align: "right" },
      cell: (c) => {
        const ms = c.getValue();
        return <span className="row-meta num">{ms ? formatMs(ms) : ""}</span>;
      },
    }),
    helper.accessor("status", {
      id: "status",
      header: "status",
      cell: (c) => (
        <span className={`chip ${STATUS_TONE[c.getValue()] ?? "muted"}`}>
          {c.getValue()}
        </span>
      ),
    }),
    helper.accessor((exp) => exp.progress.evaluated, {
      id: "cases",
      header: "cases",
      meta: { align: "right", title: "cases evaluated / pinned" },
      cell: (c) => {
        const progress = c.row.original.progress;
        return (
          <span
            className={`row-meta num ${isIncomplete(progress) ? "text-warn" : ""}`}
            title={progressLabel(progress)}
          >
            {shortProgress(progress)}
          </span>
        );
      },
    }),
    helper.accessor((exp) => agentTag(exp), {
      id: "agent",
      header: "agent",
      cell: (c) => {
        const exp = c.row.original;
        return (
          <span className="flex flex-col items-start" title={c.getValue()}>
            <span className="flex items-center gap-1">
              {splitRef(exp.agent).name}
              <WorkspaceChip workspace={exp.workspace} />
            </span>
            {exp.config.model && (
              <span className="row-meta">
                {exp.config.model.split("/").pop()}
              </span>
            )}
          </span>
        );
      },
    }),
    helper.accessor(
      (exp) => splitLabel({ selection: exp.config.selection }) ?? "",
      {
        id: "split",
        header: "split",
        meta: { wrap: true, title: "dataset split this experiment ran on" },
        cell: (c) => <span className="row-meta">{c.getValue()}</span>,
      },
    ),
    helper.accessor((exp) => agentRevision(exp).sha ?? "", {
      id: "rev",
      header: "rev",
      meta: { title: "harness git revision, and the cases revision" },
      cell: (c) => {
        const exp = c.row.original;
        const rev = agentRevision(exp);
        const cases = exp.config.revision;
        return (
          <span className="flex flex-col">
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
      },
    }),
    helper.accessor("created_by", {
      id: "user",
      header: "user",
      cell: (c) => <UserPill user={c.getValue()} />,
    }),
    helper.accessor(
      (exp) => elapsedMs(exp.started_at, exp.ended_at) ?? undefined,
      {
        id: "took",
        header: "took",
        meta: { align: "right", title: "wall time from start to end" },
        sortUndefined: "last",
        cell: (c) => {
          const took = c.getValue();
          return (
            <span className="row-meta num">
              {took !== undefined ? formatElapsed(took) : ""}
            </span>
          );
        },
      },
    ),
    helper.accessor("started_at", {
      id: "started",
      header: "started",
      meta: { align: "right", wrap: true, className: "min-w-28" },
      cell: (c) => (
        <span className="row-meta">
          {fmtWhen(c.getValue()) ?? ""}
          {c.row.original.repetitions > 1
            ? ` ×${c.row.original.repetitions}`
            : ""}
        </span>
      ),
    }),
  ];
}
