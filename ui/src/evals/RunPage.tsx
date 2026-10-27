import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, isExperimentId, isMissing } from "../lib/api";
import { answerText, fmtGauge, outputText, questionText } from "../lib/format";
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
  const [status, setStatus] = useState<ExperimentInfo | null>(null);
  useFollowWorkspace(status?.workspace);
  const [runs, setRuns] = useState<RunDetail[] | null>(null);
  const [example, setExample] = useState<CaseView | null>(null);
  const [missing, setMissing] = useState(false);
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
  const [trajectory, setTrajectory] = useState<AtifTrajectory | null>(null);
  const [trajectoryError, setTrajectoryError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const validId = isExperimentId(experimentId);
  useEffect(() => {
    setMissing(false);
    if (!validId) return;
    api<ExperimentInfo>(`/experiments/${encodeURIComponent(experimentId)}`)
      .then(setStatus)
      .catch((e) => (isMissing(e) ? setMissing(true) : setError(String(e))));
    // The case version PINNED to this experiment — a later case edit or
    // overlay sync must not change what this page shows beside old scores.
    api<CaseView[]>(`/experiments/${encodeURIComponent(experimentId)}/cases`)
      .then((examples) =>
        setExample(examples.find((ex) => ex.case_id === caseId) ?? null),
      )
      .catch(() => {});
    api<RunDetail[]>(`/experiments/${encodeURIComponent(experimentId)}/runs`)
      .then((all) => setRuns(all.filter((r) => r.case_id === caseId)))
      .catch((e) => (isMissing(e) ? setMissing(true) : setError(String(e))));
  }, [experimentId, caseId, validId]);

  const run =
    runs?.find((r) => r.repetition === repetition) ?? runs?.[0] ?? null;

  useEffect(() => {
    setTrajectory(null);
    setTrajectoryError(null);
  }, [run]);

  useEffect(() => {
    if (
      !(TRAJECTORY_TABS as readonly string[]).includes(tab) ||
      !run ||
      trajectory ||
      trajectoryError
    )
      return;
    api<AtifTrajectory>(
      `/experiments/${encodeURIComponent(experimentId)}/runs/${run.experiment_run_id}/trajectory`,
    )
      .then(setTrajectory)
      .catch((e) => setTrajectoryError(String(e)));
  }, [tab, run, experimentId, trajectory, trajectoryError]);

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
    <div className="page flex h-full flex-col">
      <div className="toolbar mb-2">
        <h1 className="max-w-160 truncate font-mono text-head font-semibold">
          {caseId}
        </h1>
        {harnessRunId && (
          <span className="row-meta font-mono" title="harness run id">
            <CopyText value={harnessRunId} />
          </span>
        )}
        {tag && (
          <span className="chip muted" title="agent · model">
            {tag}
          </span>
        )}
        {badge && (
          <span className="chip muted" title="trajectory source and mapping">
            {badge}
          </span>
        )}
        {record && <TimeSpan start={record.started_at} end={record.ended_at} />}
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
          className={`btn btn-quiet text-brand ${status?.config.judge?.model ? "" : "ml-auto"}`}
          to={`/experiments/${encodeURIComponent(experimentId)}`}
        >
          Experiment ↗
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
                  <div className="card p-4">
                    <Markdown text={questionText(example.input)} />
                  </div>
                </div>
                <div className="min-w-0">
                  <FixtureList files={example.files} />
                </div>
              </div>
            )}

            {structured && (
              <>
                <div className="eyebrow">Output</div>
                <div className="card p-4">
                  <PreBlock text={structured} />
                </div>
              </>
            )}

            {(hasAnswer || !structured) && (
              <>
                <div className="eyebrow">Answer</div>
                <div className="card p-4 lg:px-8 lg:py-6">
                  <Markdown text={answerText(record?.answer)} />
                </div>
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
  );
}
