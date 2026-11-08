import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { api, apiJson, isExperimentId, isMissing, qs } from "../lib/api";
import { refreshQueries, useApi } from "../lib/query";
import { CASE_STATE_LABEL, CASE_STATE_TONE, caseList } from "../lib/caseStates";
import {
  STATUS_TONE,
  canResume,
  isActive,
  reevaluateStrandsCases,
  resumeHint,
} from "../lib/experimentStatus";
import { selectionLabel } from "../lib/format";
import { agentCommitUrl } from "../lib/meta";
import { hasColumn, rowFailing } from "../lib/scorecard";
import { BY_TYPE, gaugeGroup, scoreGroups } from "../lib/scoreSummary";
import type { AgentInfo, CaseView, ExperimentInfo, Report } from "../lib/types";
import { CopyText } from "../lib/ui";
import { NotFound } from "../shell/NotFound";
import { RevisionChip, TimeSpan, WorkspaceChip } from "./bits";
import { ComparePicker } from "./ComparePicker";
import { agentRevision, agentTag } from "./experimentRow";
import { ProgressBar } from "./ProgressBar";
import { ScoreMatrix } from "./ScoreMatrix";
import { BucketCards, CostSpeedLine } from "./ScoreSummary";
import { MultiSelect, Select } from "../lib/Select";
import { hasAnyTag, sortedTags, tagCounts } from "../lib/tags";
import { useFollowWorkspace } from "../shell/workspace";

function ScorecardSkeleton() {
  return (
    <>
      <div className="eyebrow">Scores</div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }, (_, i) => (
          <div className="tile tile-hero" key={i}>
            <div className="skeleton h-3 w-24" />
            <div className="skeleton mt-2 h-7 w-16" />
          </div>
        ))}
      </div>
      <div className="eyebrow">Cost &amp; speed</div>
      <div className="tile-grid">
        {Array.from({ length: 10 }, (_, i) => (
          <div className="tile" key={i}>
            <div className="skeleton h-3 w-20" />
            <div className="skeleton mt-2 h-4 w-10" />
          </div>
        ))}
      </div>
      <div className="eyebrow">Cases</div>
      <div className="card">
        <div className="rows">
          {Array.from({ length: 12 }, (_, i) => (
            <div
              key={i}
              className="row grid-cols-[minmax(0,1fr)_repeat(5,4rem)]"
            >
              <span
                className="skeleton h-3.5"
                style={{ width: `${30 + i * 4}%` }}
              />
              {Array.from({ length: 5 }, (_, j) => (
                <span className="skeleton h-3.5" key={j} />
              ))}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}

// A fresh page per experiment, so a confirmation left open on one can't act on
// the next after Back or Forward.
export function ExperimentPage() {
  const { experimentId } = useParams();
  return <ExperimentView key={experimentId} />;
}

function ExperimentView() {
  const { experimentId = "" } = useParams();
  const [params] = useSearchParams();
  const baselineId = params.get("baseline");

  const [actionError, setActionError] = useState<string | null>(null);
  const [failingOnly, setFailingOnly] = useState(false);
  const [evaluator, setEvaluator] = useState("");
  const [tagFilter, setTagFilter] = useState<string[]>([]);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [confirmingReevaluate, setConfirmingReevaluate] = useState(false);

  const validId = isExperimentId(experimentId);
  const base = `/experiments/${encodeURIComponent(experimentId)}`;
  // Status and scorecard load together, and an active experiment reloads
  // both every 3 s until it settles, so the last scorecard is the final one.
  const live = useQuery({
    queryKey: [base, "live", baselineId],
    queryFn: async () => {
      const [status, report] = await Promise.all([
        api<ExperimentInfo>(base),
        api<Report>(
          `${base}/scorecard${qs({ baseline_id: baselineId ?? undefined })}`,
        ),
      ]);
      return { status, report };
    },
    enabled: validId,
    refetchInterval: (q) =>
      q.state.data && isActive(q.state.data.status.status) ? 3000 : false,
  });
  const status = live.data?.status ?? null;
  const report = live.data?.report ?? null;
  useFollowWorkspace(status?.workspace);
  const missing = isMissing(live.error);
  const error =
    actionError ?? (live.error && !missing ? String(live.error) : null);
  const pinned = useApi<CaseView[]>(validId ? `${base}/cases` : null).data;
  const agentRef = status?.agent;
  const driver =
    useApi<AgentInfo>(
      agentRef ? `/agents/${encodeURIComponent(agentRef)}` : null,
    ).data?.driver ?? null;

  const action = async (verb: "evaluate" | "resume") => {
    setBusyAction(verb);
    setActionError(null);
    try {
      await apiJson(`${base}/${verb}`, "POST");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyAction(null);
      void refreshQueries();
    }
  };

  if (missing || !validId)
    return (
      <NotFound
        what={`experiment ${experimentId}`}
        up={{ label: "All experiments", to: "/experiments" }}
      />
    );
  const config = status?.config;
  const tag = status ? agentTag(status) : null;
  const selection = config
    ? selectionLabel({ selection: config.selection })
    : null;
  const rev = status ? agentRevision(status) : {};
  const cases = config?.revision ?? null;
  const drives = !!status && status.can_change && status.server_may_drive;
  const allGroups = report ? scoreGroups(report) : [];
  const gauges = report ? gaugeGroup(report) : null;
  const groups = [...allGroups, ...(gauges ? [gauges] : [])];
  const hiddenByDefault = groups
    .filter((g) => g.label === BY_TYPE || g.kind === "gauge")
    .flatMap((g) => g.cols);
  const listed = caseList(
    (pinned ?? []).map((c) => c.case_id),
    status?.case_states ?? {},
    report,
  );
  const stateOf = new Map(listed.map((r) => [r.case_id, r.state]));
  const tagsOf = new Map((pinned ?? []).map((c) => [c.case_id, c.split_tags]));
  const tags = tagCounts(pinned ?? []);
  const rows = listed.filter((r) => {
    if (tagFilter.length && !hasAnyTag(tagsOf.get(r.case_id) ?? [], tagFilter))
      return false;
    if ((failingOnly || evaluator) && !r.row) return false;
    if (failingOnly && r.row && !rowFailing(r.row)) return false;
    if (evaluator && r.row && !hasColumn(r.row, evaluator)) return false;
    return true;
  });

  return (
    <div className="page">
      <div className="toolbar mb-3 flex-wrap">
        <nav className="flex shrink-0 items-baseline gap-1.5">
          {status && (
            <>
              <Link
                className="shrink-0 text-meta text-ink-3 transition-colors hover:text-brand"
                to={`/datasets/${encodeURIComponent(status.dataset)}`}
              >
                {status.dataset}
              </Link>
              <span className="text-ink-4" aria-hidden>
                ›
              </span>
            </>
          )}
          <h1
            className="max-w-160 truncate font-mono text-head font-semibold"
            title={`experiment ${experimentId}`}
          >
            {status ? `#${status.number}` : experimentId}
          </h1>
          <span className="row-meta">
            <CopyText value={experimentId} />
          </span>
        </nav>
        {tag && <span className="chip muted">{tag}</span>}
        {config?.snapshot.trajectory_source === "otel" && (
          <span
            className="chip muted"
            title="trajectories come from collected traces"
          >
            traces
          </span>
        )}
        <WorkspaceChip workspace={status?.workspace} />
        {selection && (
          <span className="chip muted" title="the cases this experiment ran on">
            cases: {selection}
          </span>
        )}
        <RevisionChip
          sha={rev.sha}
          branch={rev.branch}
          url={agentCommitUrl(status?.agent, rev.sha)}
        />
        {cases?.sha && (
          <span
            className="chip muted font-mono"
            title={`cases ${cases.branch ? `${cases.branch} @ ` : ""}${cases.sha}${cases.dirty ? " (dirty)" : ""}`}
          >
            cases {cases.sha.slice(0, 8)}
            {cases.dirty ? "*" : ""}
          </span>
        )}
        {config?.judge?.model && (
          <span
            id="judge"
            className="chip muted font-mono"
            title={[
              `judge ${config.judge.model}`,
              config.judge.provider
                ? `provider ${config.judge.provider} (scored on the server)`
                : `${config.judge.base_url} (own key, scored locally)`,
              config.judge.reasoning_effort &&
                `effort ${config.judge.reasoning_effort}`,
              config.judge.max_tokens != null &&
                `max tokens ${config.judge.max_tokens}`,
            ]
              .filter(Boolean)
              .join(" · ")}
          >
            judge {config.judge.model}
            {config.judge.reasoning_effort
              ? ` · ${config.judge.reasoning_effort}`
              : ""}
          </span>
        )}
        {config?.snapshot.evalkit_version && (
          <span
            className="row-meta font-mono"
            title="evalkit version that ran this experiment"
          >
            evalkit {config.snapshot.evalkit_version}
          </span>
        )}
        {status && (
          <span className={`chip ${STATUS_TONE[status.status] ?? "muted"}`}>
            {status.status}
          </span>
        )}
        {status && <ProgressBar progress={status.progress} />}
        {status && (
          <TimeSpan
            start={status.started_at}
            end={status.ended_at}
            running={isActive(status.status)}
          />
        )}
        {status && isActive(status.status) && status.lease_holder && (
          <span className="row-meta" title="who is running this experiment">
            held by <span className="font-mono">{status.lease_holder}</span>
          </span>
        )}
        {baselineId && (
          <span className="row-meta">
            vs baseline <span className="font-mono">{baselineId}</span>
          </span>
        )}
        <span className="ml-auto flex items-center gap-2">
          {status && (
            <ComparePicker dataset={status.dataset} experimentId={status.id} />
          )}
          {status && drives && (
            <button
              className="btn"
              disabled={busyAction !== null || isActive(status.status)}
              onClick={() =>
                reevaluateStrandsCases(status)
                  ? setConfirmingReevaluate(true)
                  : action("evaluate")
              }
              title="re-run the evaluate phase on recorded runs"
            >
              {busyAction === "evaluate" ? "Evaluating…" : "Re-evaluate"}
            </button>
          )}
          {status && drives && canResume(status) && (
            <button
              className="btn"
              disabled={busyAction !== null}
              onClick={() => action("resume")}
            >
              {busyAction === "resume" ? "Resuming…" : "Resume"}
            </button>
          )}
          {status && !drives && status.can_change && canResume(status) && (
            <span className="row-meta font-mono">
              <CopyText value={resumeHint(status, driver ?? "cli")} />
            </span>
          )}
        </span>
      </div>

      {status?.status_error && (
        <div className="pane-status error">{status.status_error}</div>
      )}
      {error && <div className="pane-status error">{error}</div>}
      {!report && !error && <ScorecardSkeleton />}

      {report && (
        <>
          <div className="eyebrow">Scores</div>
          <BucketCards
            report={report}
            onPick={(name) => {
              setEvaluator(name);
              document
                .getElementById(`col-${name}`)
                ?.scrollIntoView({ block: "nearest", inline: "center" });
            }}
          />
          <CostSpeedLine report={report} />
          <div className="eyebrow">Cases</div>
          <ScoreMatrix
            id="experiment-cases"
            report={report}
            groups={groups}
            defaultHidden={hiddenByDefault}
            toggles={[
              {
                label: "by type",
                title:
                  "show the answer-rubric rates broken down by rubric type",
                cols: allGroups.find((g) => g.label === BY_TYPE)?.cols ?? [],
              },
              {
                label: "gauges",
                title: "show per-case gauges: cost, time, tokens, tool calls",
                cols: gauges?.cols ?? [],
              },
            ]}
            sortBy={evaluator}
            toolbar={
              <>
                <Select
                  ariaLabel="evaluator"
                  value={evaluator}
                  onChange={setEvaluator}
                  title="focus one evaluator: sorts by it, worst first, and drops cases it never scored"
                  options={[
                    { value: "", label: "All evaluators" },
                    ...report.evaluator_columns.map((c) => ({
                      value: c,
                      label: c,
                    })),
                  ]}
                />
                {tags.length > 0 && (
                  <MultiSelect
                    ariaLabel="tags"
                    placeholder="All tags"
                    title="show only cases carrying any of these tags"
                    values={tagFilter}
                    onChange={(picked) => setTagFilter(sortedTags(picked))}
                    options={tags.map((t) => ({
                      value: t.name,
                      label: t.name,
                      hint: t.count,
                    }))}
                  />
                )}
                <label className="check">
                  <input
                    type="checkbox"
                    checked={failingOnly}
                    onChange={(e) => setFailingOnly(e.target.checked)}
                  />
                  failing only
                </label>
              </>
            }
            rows={rows.map(({ case_id, row }) => ({
              case_id,
              row: row ?? null,
            }))}
            filtered={rows.length !== listed.length}
            caseCell={({ case_id, row }) => {
              const state = stateOf.get(case_id)!;
              return (
                <>
                  {state === "pending" ? (
                    <span title="this case has not run yet">{case_id}</span>
                  ) : (
                    <Link
                      to={`/experiments/${encodeURIComponent(experimentId)}/${encodeURIComponent(case_id)}`}
                      target="_blank"
                      rel="noopener"
                      className="text-inherit"
                      title="open this case's run in a new tab"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {case_id}
                    </Link>
                  )}
                  <span className={`chip ${CASE_STATE_TONE[state]} ml-2`}>
                    {CASE_STATE_LABEL[state]}
                  </span>
                  {status && (
                    <Link
                      to={`/datasets/${encodeURIComponent(status.dataset)}/cases/${encodeURIComponent(case_id)}`}
                      target="_blank"
                      rel="noreferrer"
                      className="ml-2 text-micro text-ink-3 hover:text-brand hover:underline"
                      title="open the case definition in its dataset (new tab)"
                      onClick={(e) => e.stopPropagation()}
                    >
                      def ↗
                    </Link>
                  )}
                  {row && row.run_errors.length > 0 && (
                    <span
                      className="chip chip-fail ml-2"
                      title={row.run_errors.join("\n")}
                    >
                      run error
                    </span>
                  )}
                </>
              );
            }}
          />
          <div className="num mt-2 text-micro text-ink-3">
            {rows.length === listed.length
              ? `${listed.length} cases`
              : `${rows.length}/${listed.length} cases`}
            {report.baseline_id ? ` · vs ${report.baseline_id}` : ""}
          </div>
        </>
      )}
      {report && listed.length === 0 && (
        <div className="pane-status">This experiment pinned no cases.</div>
      )}
      <AlertDialog
        open={confirmingReevaluate}
        onOpenChange={setConfirmingReevaluate}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Re-evaluate anyway?</AlertDialogTitle>
            <AlertDialogDescription>
              Some runs of this experiment have not completed. Resume first to
              run them; re-evaluating now scores only the runs that finished.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                // The closing dialog stays clickable; a double click acts once.
                if (!confirmingReevaluate) return;
                setConfirmingReevaluate(false);
                void action("evaluate");
              }}
            >
              Re-evaluate
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
