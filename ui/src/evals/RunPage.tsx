import { useState } from "react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { ArrowUpRight } from "lucide-react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { isExperimentId, isMissing } from "../lib/api";
import { answerText, fmtGauge, outputText, questionText } from "../lib/format";
import { useApi } from "../lib/query";
import { trajectoryAvailable, trajectoryBadge } from "../lib/runs";
import { gaugeRows, toEvalScores } from "../lib/scores";
import type {
  AtifTrajectory,
  CaseView,
  ExperimentInfo,
  RunDetail,
} from "../lib/types";
import { CopyText, InfoTip, Tabs } from "../lib/ui";
import { NotFound } from "../shell/NotFound";
import { agentTag } from "./experimentRow";
import {
  EvaluatorScores,
  FixtureList,
  Markdown,
  PreBlock,
  Tag,
  TimeSpan,
} from "./bits";
import { RunTabs, TRAJECTORY_TABS } from "../runs/RunTabs";
import { Select } from "../lib/Select";
import { useFollowWorkspace } from "../shell/workspace";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";

const TABS = [
  "case",
  "scores",
  "trajectory",
  "workspace",
  "cost",
  "timing",
] as const;
type Tab = (typeof TABS)[number];

export function RunPage() {
  const { experimentId = "", exampleId: caseId = "" } = useParams();
  const validId = isExperimentId(experimentId);
  const base = `/experiments/${encodeURIComponent(experimentId)}`;
  const statusQuery = useApi<ExperimentInfo>(validId ? base : null);
  const status = statusQuery.data ?? null;
  useFollowWorkspace(status?.workspace);
  const runsQuery = useApi<RunDetail[]>(validId ? `${base}/runs` : null, {
    select: (all) => all.filter((r) => r.case_id === caseId),
  });
  const runs = runsQuery.data ?? null;
  // The case version PINNED to this experiment — a later case edit or
  // overlay sync must not change what this page shows beside old scores.
  const example =
    useApi<CaseView[]>(validId ? `${base}/cases` : null).data?.find(
      (ex) => ex.case_id === caseId,
    ) ?? null;
  const missing = isMissing(statusQuery.error) || isMissing(runsQuery.error);
  const loadError = statusQuery.error ?? runsQuery.error;
  const error = loadError && !missing ? String(loadError) : null;
  const [repetition, setRepetition] = useState(1);
  const [params, setParams] = useSearchParams();
  const rawTab = params.get("tab") ?? "";
  const tab: Tab = (TABS as readonly string[]).includes(rawTab)
    ? (rawTab as Tab)
    : "case";
  const setTab = (next: Tab) => {
    const nextParams = new URLSearchParams(params);
    if (next === "case") nextParams.delete("tab");
    else nextParams.set("tab", next);
    setParams(nextParams, { replace: true });
  };
  const run =
    runs?.find((r) => r.repetition === repetition) ?? runs?.[0] ?? null;

  // Loads once a trajectory tab opens, and once per run: a disabled query keeps
  // its data, so the toolbar badge stays on the other tabs. Dropped on close.
  const trajectoryQuery = useApi<AtifTrajectory>(
    run ? `${base}/runs/${run.experiment_run_id}/trajectory` : null,
    {
      enabled: (TRAJECTORY_TABS as readonly string[]).includes(tab),
      staleTime: Infinity,
      gcTime: 0,
    },
  );
  const trajectory = trajectoryQuery.data ?? null;
  const trajectoryError = trajectoryQuery.error
    ? String(trajectoryQuery.error)
    : null;

  if (missing || !validId)
    return (
      <NotFound
        what={`experiment ${experimentId}`}
        up={{ label: "All experiments", to: "/experiments" }}
      />
    );
  if (error) return <div className="pane-status error">{error}</div>;
  if (!runs) return <div className="pane-status">Loading run…</div>;
  if (runs.length === 0 && example)
    return (
      <div className="page">
        <div className="pane-status">
          This case has not run yet in experiment {experimentId}.{" "}
          <Link
            className="text-brand"
            to={`/experiments/${encodeURIComponent(experimentId)}`}
          >
            Back to the experiment
          </Link>
        </div>
      </div>
    );
  if (runs.length === 0)
    return (
      <NotFound
        what={`run of ${caseId} in experiment ${experimentId}`}
        up={{
          label: `experiment ${experimentId}`,
          to: `/experiments/${encodeURIComponent(experimentId)}`,
        }}
      />
    );

  const record = run?.agent_run ?? null;
  const scores = run ? toEvalScores(run.evaluations) : {};
  const gauges = run ? gaugeRows(run.evaluations) : [];
  const structured = outputText(record?.output);
  const hasAnswer = record?.answer != null;
  const agent = status?.agent;
  const tag = status ? agentTag(status) : null;
  const harnessRunId = record?.harness_run_id ?? null;
  const trajectoryStatus = record?.trajectory_status ?? null;
  const mappingVersion = trajectory?.extra?.mapping_version;
  const badge = trajectoryBadge(
    { trajectory_source: status?.config.snapshot.trajectory_source },
    trajectoryStatus,
    typeof mappingVersion === "number" ? mappingVersion : null,
  );
  const workspaceRunId = harnessRunId;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        breadcrumbs={[
          { label: "Experiments", to: "/experiments" },
          {
            label: status
              ? `${status.dataset} #${status.number}`
              : experimentId,
            to: `/experiments/${encodeURIComponent(experimentId)}`,
          },
        ]}
        title={<span className="font-mono">{caseId}</span>}
      />
      <div className="page flex min-h-0 flex-1 flex-col">
        <div className="toolbar mb-2">
          {harnessRunId && (
            <span className="row-meta font-mono" title="harness run id">
              <CopyText value={harnessRunId} />
            </span>
          )}
          {tag && (
            <Badge variant="secondary" title="agent · model">
              {tag}
            </Badge>
          )}
          {badge && (
            <Badge variant="secondary" title="trajectory source and mapping">
              {badge}
            </Badge>
          )}
          {record && (
            <TimeSpan start={record.started_at} end={record.ended_at} />
          )}
          {(example?.split_tags ?? []).map((t) => (
            <Tag key={t}>{t}</Tag>
          ))}
          {runs.length > 1 && (
            <Select
              ariaLabel="repetition"
              value={repetition}
              onChange={setRepetition}
              options={runs.map((r) => ({
                value: r.repetition,
                label: `repetition ${r.repetition}`,
              }))}
            />
          )}
          {status?.config.judge?.model && (
            <Link
              className="row-meta ml-auto font-mono hover:underline"
              to={`/experiments/${encodeURIComponent(experimentId)}#judge`}
              title="one judge scores the whole experiment; its settings are on the experiment page"
            >
              judge {status.config.judge.model}
            </Link>
          )}
          <Link
            className={cn(
              buttonVariants({ variant: "link" }),
              !status?.config.judge?.model && "ml-auto",
            )}
            to={`/experiments/${encodeURIComponent(experimentId)}`}
          >
            Experiment <ArrowUpRight size={12} />
          </Link>
        </div>

        {record?.error && (
          <div className="pane-status error">run error: {record.error}</div>
        )}

        {gauges.length > 0 && (
          <div className="num mt-1 flex flex-wrap gap-3 text-micro text-ink-3">
            {gauges
              .filter((g) => g.value !== null)
              .map((g) => (
                <span
                  key={g.name}
                  className="inline-flex items-center gap-1"
                  title={g.explanation ?? g.name}
                >
                  {g.name.replace(/^gauge_/, "").replaceAll("_", " ")}{" "}
                  {fmtGauge(g.name, g.value)}
                  <InfoTip term={g.name} />
                </span>
              ))}
          </div>
        )}

        <div className="mt-3">
          <Tabs<Tab>
            tabs={[
              { id: "case", label: "Case" },
              { id: "scores", label: "Scores" },
              {
                id: "trajectory",
                label: "Trajectory",
                disabled: !trajectoryAvailable(trajectoryStatus),
                title: trajectoryAvailable(trajectoryStatus)
                  ? undefined
                  : `trajectory ${trajectoryStatus ?? "not recorded"}`,
              },
              {
                id: "workspace",
                label: "Workspace",
                disabled: !agent || !workspaceRunId,
                title: workspaceRunId
                  ? undefined
                  : "no run id recorded for this run",
              },
              {
                id: "cost",
                label: "Cost",
                disabled: !trajectoryAvailable(trajectoryStatus),
                title: trajectoryAvailable(trajectoryStatus)
                  ? undefined
                  : `trajectory ${trajectoryStatus ?? "not recorded"}`,
              },
              {
                id: "timing",
                label: "Timing",
                disabled: !trajectoryAvailable(trajectoryStatus),
                title: trajectoryAvailable(trajectoryStatus)
                  ? undefined
                  : `trajectory ${trajectoryStatus ?? "not recorded"}`,
              },
            ]}
            active={tab}
            onSelect={setTab}
          />
        </div>

        <div className="mt-3 flex min-h-96 flex-1 flex-col">
          {tab === "case" ? (
            <div className="shrink-0">
              {example && (
                <div className="grid gap-x-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
                  <div className="min-w-0">
                    <div className="eyebrow">Question</div>
                    <Card>
                      <CardContent>
                        <Markdown text={questionText(example.input)} />
                      </CardContent>
                    </Card>
                  </div>
                  <div className="min-w-0">
                    <FixtureList files={example.files} />
                  </div>
                </div>
              )}

              {structured && (
                <>
                  <div className="eyebrow">Output</div>
                  <Card>
                    <CardContent>
                      <PreBlock text={structured} />
                    </CardContent>
                  </Card>
                </>
              )}

              {(hasAnswer || !structured) && (
                <>
                  <div className="eyebrow">Answer</div>
                  <Card className="lg:py-6">
                    <CardContent className="lg:px-8">
                      <Markdown text={answerText(record?.answer)} />
                    </CardContent>
                  </Card>
                  <details className="mt-1 text-micro">
                    <summary className="cursor-pointer text-n-400">
                      raw answer
                    </summary>
                    <PreBlock
                      text={answerText(record?.answer)}
                      language="markdown"
                    />
                  </details>
                </>
              )}
            </div>
          ) : tab === "scores" ? (
            <EvaluatorScores
              evaluators={example?.effective_evaluators ?? {}}
              scores={scores}
            />
          ) : (
            <RunTabs
              tab={tab}
              trajectory={trajectory}
              error={trajectoryError}
              agent={agent}
              runId={workspaceRunId}
            />
          )}
        </div>
      </div>
    </div>
  );
}
