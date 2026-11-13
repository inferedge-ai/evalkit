import { Markdown } from "@/components/widgets/Markdown";
import { useState } from "react";
import { cn } from "@/lib/utils";
import { buttonVariants } from "@/components/ui/button";
import { ArrowUpRight, CircleDashed, ListChecks } from "lucide-react";
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
import { EvaluatorScores, FixtureList, PreBlock, Tag, TimeSpan } from "./bits";
import { RunTabs, TRAJECTORY_TABS } from "../runs/RunTabs";
import { Select } from "../lib/Select";
import { useFollowWorkspace } from "../shell/workspace";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const TABS = [
  "case",
  "scores",
  "trajectory",
  "workspace",
  "cost",
  "timing",
] as const;
type Tab = (typeof TABS)[number];

const TAB_LABELS: Record<Tab, string> = {
  case: "Case",
  scores: "Scores",
  trajectory: "Trajectory",
  workspace: "Workspace",
  cost: "Cost",
  timing: "Timing",
};

const LOADING_TABS = TABS.map((id) => ({
  id,
  label: TAB_LABELS[id],
  disabled: id !== "case" && id !== "scores",
}));

function RunSkeleton({
  tab,
  onSelect,
}: {
  tab: Tab;
  onSelect: (tab: Tab) => void;
}) {
  return (
    <>
      <div className="toolbar mb-2">
        <Skeleton className="h-4 w-24" />
        <Skeleton className="h-5 w-36 rounded-4xl" />
        <Skeleton className="h-4 w-44" />
        <Skeleton className="ml-auto h-4 w-32" />
      </div>
      <Skeleton className="mt-1 h-3 w-72 max-w-full" />
      <div className="mt-3">
        <Tabs<Tab> tabs={LOADING_TABS} active={tab} onSelect={onSelect} />
      </div>
      <div className="mt-3">
        {tab === "case" && (
          <>
            <div className="grid gap-x-5 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
              <div className="min-w-0">
                <div className="eyebrow">Question</div>
                <Card>
                  <CardContent className="flex flex-col gap-2">
                    <Skeleton className="h-4 w-4/5" />
                    <Skeleton className="h-4 w-2/5" />
                  </CardContent>
                </Card>
              </div>
            </div>
            <div className="eyebrow">Answer</div>
            <Card className="lg:py-6">
              <CardContent className="flex flex-col gap-2 lg:px-8">
                <Skeleton className="h-4 w-full" />
                <Skeleton className="h-4 w-11/12" />
                <Skeleton className="h-4 w-3/5" />
              </CardContent>
            </Card>
          </>
        )}
        {tab === "scores" && (
          <>
            <Skeleton className="mt-3 mb-1.5 h-4 w-24" />
            <Skeleton className="mb-2 h-20 w-full rounded-xl" />
            <Skeleton className="mb-2 h-20 w-full rounded-xl" />
          </>
        )}
      </div>
    </>
  );
}

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
  const casesQuery = useApi<CaseView[]>(validId ? `${base}/cases` : null);
  const example = casesQuery.data?.find((ex) => ex.case_id === caseId) ?? null;
  const missing = isMissing(statusQuery.error) || isMissing(runsQuery.error);
  const loadError = statusQuery.error ?? runsQuery.error ?? casesQuery.error;
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
  const header = (
    <PageHeader
      breadcrumbs={[
        { label: "Experiments", to: "/experiments" },
        {
          label: status ? `${status.dataset} #${status.number}` : experimentId,
          to: base,
        },
      ]}
      title={<span className="font-mono">{caseId}</span>}
    />
  );
  if (!runs || (runs.length === 0 && casesQuery.data === undefined))
    return (
      <>
        {header}
        <div className="page">
          {error ? (
            <div className="pane-status error">{error}</div>
          ) : (
            <RunSkeleton tab={tab} onSelect={setTab} />
          )}
        </div>
      </>
    );
  if (runs.length === 0 && example)
    return (
      <>
        {header}
        <div className="page">
          {error && <div className="pane-status error">{error}</div>}
          <Card className="py-8">
            <Empty
              icon={CircleDashed}
              title="This case has not run yet"
              description={
                <Link className="text-brand hover:underline" to={base}>
                  Back to the experiment
                </Link>
              }
            />
          </Card>
        </div>
      </>
    );
  if (runs.length === 0)
    return (
      <NotFound
        what={`run of ${caseId} in experiment ${experimentId}`}
        up={{
          label: `experiment ${experimentId}`,
          to: base,
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
      {header}
      <div className="page flex min-h-0 flex-1 flex-col">
        {error && <div className="pane-status error">{error}</div>}
        {record?.trajectory_note && (
          <div
            className="pane-status mb-2 whitespace-normal"
            title="why the trajectory is partial or unavailable, or what its mapping missed"
          >
            Trajectory {trajectoryStatus}: {record.trajectory_note}
          </div>
        )}
        <div className="toolbar mb-2">
          {harnessRunId && (
            <span
              className="row-meta flex min-w-0 font-mono"
              title="harness run id"
            >
              <CopyText value={harnessRunId} />
            </span>
          )}
          {tag && (
            <Badge variant="secondary" className="min-w-0 shrink" title={tag}>
              <span className="truncate">{tag}</span>
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
              className="row-meta ml-auto min-w-0 truncate font-mono hover:underline"
              to={`${base}#judge`}
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
            to={base}
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
              { id: "case", label: TAB_LABELS.case },
              { id: "scores", label: TAB_LABELS.scores },
              {
                id: "trajectory",
                label: TAB_LABELS.trajectory,
                disabled: !trajectoryAvailable(trajectoryStatus),
                title: trajectoryAvailable(trajectoryStatus)
                  ? undefined
                  : `trajectory ${trajectoryStatus ?? "not recorded"}`,
              },
              {
                id: "workspace",
                label: TAB_LABELS.workspace,
                disabled: !agent || !workspaceRunId,
                title: workspaceRunId
                  ? undefined
                  : "no run id recorded for this run",
              },
              {
                id: "cost",
                label: TAB_LABELS.cost,
                disabled: !trajectoryAvailable(trajectoryStatus),
                title: trajectoryAvailable(trajectoryStatus)
                  ? undefined
                  : `trajectory ${trajectoryStatus ?? "not recorded"}`,
              },
              {
                id: "timing",
                label: TAB_LABELS.timing,
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
                        <Markdown text={questionText(example.input)} math />
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
                      <Markdown text={answerText(record?.answer)} math />
                    </CardContent>
                  </Card>
                  <details className="mt-1 text-micro">
                    <summary className="cursor-pointer text-muted-foreground">
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
            example &&
            Object.keys(example.effective_evaluators).length === 0 &&
            Object.keys(scores).length === 0 ? (
              <Card className="py-8">
                <Empty
                  icon={ListChecks}
                  title="No scores for this run"
                  description="The case and its overlays set no evaluators for this agent."
                />
              </Card>
            ) : (
              <EvaluatorScores
                evaluators={example?.effective_evaluators ?? {}}
                scores={scores}
              />
            )
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
