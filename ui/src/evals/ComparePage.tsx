import { useState } from "react";
import { Button } from "@/components/ui/button";
import { ArrowUpRight, GitCompareArrows } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { isExperimentId, isMissing } from "../lib/api";
import { progressLabel } from "../lib/experimentStatus";
import { useApi } from "../lib/query";
import { NotFound } from "../shell/NotFound";
import { ToArrow } from "../lib/ui";
import { fmtDelta, fmtScore, severity } from "../lib/format";
import {
  CHANGE_LABEL,
  CHANGE_ORDER,
  compareRows,
  delta,
  failedChecks,
  hasScores,
  scoreGroups,
  type CaseChange,
  type CompareRow,
} from "../lib/scoreSummary";
import type { ExperimentInfo, Report } from "../lib/types";
import { ScoreMatrix } from "./ScoreMatrix";
import {
  CompareBucketCards,
  CostSpeedLine,
  ScoresSkeleton,
} from "./ScoreSummary";
import { Empty } from "@/components/widgets/Empty";
import { PageHeader } from "@/components/widgets/PageHeader";
import { Badge, badgeVariants } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

interface CompareResponse {
  base: Report;
  compare: Report;
}

const deltaTone = (d: number | null) =>
  d && d > 0 ? "text-ok" : d && d < 0 ? "text-err-text" : "text-ink-4";

const CASE_ROW = "row w-full grid-cols-[minmax(0,1fr)_auto_auto_auto_7rem]";

function CompareSkeleton() {
  return (
    <>
      <div className="eyebrow">Scores</div>
      <ScoresSkeleton />
      <div className="eyebrow">Cases</div>
      <Card className="rows gap-0 py-0">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className={CASE_ROW}>
            <Skeleton className="h-4" style={{ width: `${30 + i * 8}%` }} />
            <Skeleton className="h-3 w-8" />
            <Skeleton className="h-3 w-8" />
            <Skeleton className="h-3 w-8" />
            <Skeleton className="h-4 w-16 justify-self-end" />
          </div>
        ))}
      </Card>
    </>
  );
}

function CaseDetail({
  row,
  data,
  base,
  compare,
}: {
  row: CompareRow;
  data: CompareResponse;
  base: string;
  compare: string;
}) {
  const names = [
    ...new Set([
      ...data.compare.evaluator_columns,
      ...data.base.evaluator_columns,
    ]),
  ].filter(
    (n) => row.base?.scores[n] != null || row.compare?.scores[n] != null,
  );
  const runLink = (id: string, label: string) => (
    <Link
      className="inline-flex items-center gap-0.5 text-brand hover:underline"
      to={`/experiments/${encodeURIComponent(id)}/${encodeURIComponent(row.case_id)}`}
      target="_blank"
      rel="noopener"
    >
      {label} <ArrowUpRight size={12} />
    </Link>
  );
  return (
    <div className="border-t border-line-soft px-4 py-2 text-meta">
      {names.map((n, i) => {
        const d = delta(row.base?.scores[n], row.compare?.scores[n]);
        const text = row.compare?.explanations[n];
        const repeated = names
          .slice(0, i)
          .some((m) => row.compare?.explanations[m] === text);
        const why = text && !repeated ? failedChecks(text) : null;
        return (
          <div key={n} className="py-1">
            <div className="flex items-baseline gap-2">
              <span className="flex-1 font-mono">{n}</span>
              <span className="num text-ink-3">
                {fmtScore(row.base?.scores[n] ?? null)} <ToArrow />
              </span>
              <span
                className={`num sev-fg-${severity(row.compare?.scores[n] ?? null)}`}
              >
                {fmtScore(row.compare?.scores[n] ?? null)}
              </span>
              <span className={`num w-12 text-right ${deltaTone(d)}`}>
                {fmtDelta(d) ?? ""}
              </span>
            </div>
            {why && (
              <ul className="text-micro text-ink-3">
                {why.lines.map((line, j) => (
                  <li key={j} className="line-clamp-2" title={line}>
                    {line}
                  </li>
                ))}
                {why.more > 0 && <li>+{why.more} more in the run</li>}
              </ul>
            )}
          </div>
        );
      })}
      <div className="mt-1 flex gap-3">
        {row.base && runLink(base, "base run")}
        {row.compare && runLink(compare, "compare run")}
      </div>
    </div>
  );
}

export function ComparePage() {
  const [params] = useSearchParams();
  const base = params.get("base") ?? "";
  const compare = params.get("compare") ?? "";
  const [view, setView] = useState<"list" | "grid">("list");
  const [only, setOnly] = useState<CaseChange | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());

  const validIds = isExperimentId(base) && isExperimentId(compare);
  const compared = useApi<CompareResponse>(
    validIds
      ? `/experiments/compare?base=${encodeURIComponent(base)}&compare=${encodeURIComponent(compare)}`
      : null,
  );
  const baseInfo = useApi<ExperimentInfo>(
    validIds ? `/experiments/${encodeURIComponent(base)}` : null,
  );
  const compareInfo = useApi<ExperimentInfo>(
    validIds ? `/experiments/${encodeURIComponent(compare)}` : null,
  );
  const data = compared.data ?? null;
  const info =
    baseInfo.data && compareInfo.data
      ? { base: baseInfo.data, compare: compareInfo.data }
      : null;
  const missing = isMissing(compared.error);
  const loadError =
    (missing ? null : compared.error) ?? baseInfo.error ?? compareInfo.error;
  const error = loadError ? String(loadError) : null;

  if (missing || (base && compare && !validIds))
    return (
      <NotFound
        what={`experiment ${base} or ${compare}`}
        up={{ label: "All experiments", to: "/experiments" }}
      />
    );

  const header = (
    <PageHeader
      breadcrumbs={[{ label: "Experiments", to: "/experiments" }]}
      title="Compare"
    />
  );
  if (!base || !compare)
    return (
      <>
        {header}
        <div className="page">
          <Card className="py-8">
            <Empty
              icon={GitCompareArrows}
              title="Pick two experiments to compare"
              description="On an experiment's page, pick another experiment on its dataset under Compare with."
            />
          </Card>
        </div>
      </>
    );
  if (!data)
    return (
      <>
        {header}
        <div className="page">
          {error ? (
            <div className="pane-status error">{error}</div>
          ) : (
            <CompareSkeleton />
          )}
        </div>
      </>
    );

  const rows = compareRows(data.base, data.compare);
  const counts = new Map<CaseChange, number>();
  rows.forEach((r) => counts.set(r.change, (counts.get(r.change) ?? 0) + 1));
  const shown = only ? rows.filter((r) => r.change === only) : rows;
  const toggle = (caseId: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(caseId)) next.delete(caseId);
      else next.add(caseId);
      return next;
    });

  return (
    <>
      {header}
      <div className="page">
        {error && <div className="pane-status error">{error}</div>}
        <div className="toolbar mb-3">
          <Link
            className={cn(badgeVariants({ variant: "secondary" }), "font-mono")}
            to={`/experiments/${base}`}
          >
            base: {data.base.dataset} {data.base.name}
          </Link>
          <span className="text-ink-4">
            <ToArrow />
          </span>
          <Link
            className={cn(badgeVariants({ variant: "secondary" }), "font-mono")}
            to={`/experiments/${compare}`}
          >
            {data.compare.dataset} {data.compare.name}
          </Link>
        </div>

        {(["base", "compare"] as const).map((side) =>
          hasScores(data[side]) ? null : (
            <div key={side} className="pane-status">
              {side} {data[side].name} has no scores
              {info && (
                <>
                  : {info[side].status}, {progressLabel(info[side].progress)}
                  {info[side].status_error
                    ? ` (${info[side].status_error})`
                    : ""}
                </>
              )}
            </div>
          ),
        )}

        <div className="eyebrow">Scores</div>
        <CompareBucketCards base={data.base} compare={data.compare} />
        <CostSpeedLine report={data.compare} base={data.base} />

        <div className="eyebrow">Cases</div>
        <div className="toolbar mb-1">
          {CHANGE_ORDER.filter((c) => counts.get(c)).map((c) => (
            <button
              key={c}
              type="button"
              className={badgeVariants({
                variant: only === c ? "info" : "secondary",
              })}
              onClick={() => setOnly(only === c ? null : c)}
              title={
                only === c
                  ? "show all cases"
                  : `show only ${CHANGE_LABEL[c]} cases`
              }
            >
              {counts.get(c)} {CHANGE_LABEL[c]}
            </button>
          ))}
          <span className="ml-auto flex gap-1">
            <Button
              size="sm"
              variant={view === "list" ? "default" : "outline"}
              onClick={() => setView("list")}
            >
              List
            </Button>
            <Button
              size="sm"
              variant={view === "grid" ? "default" : "outline"}
              onClick={() => setView("grid")}
            >
              All evaluators
            </Button>
          </span>
        </div>

        {view === "list" && (
          <Card className="gap-0 py-0">
            <div className="rows">
              {shown.map((r) => (
                <div key={r.case_id}>
                  <button
                    type="button"
                    className={cn(CASE_ROW, "text-left hover:bg-fill")}
                    onClick={() => toggle(r.case_id)}
                    aria-expanded={open.has(r.case_id)}
                  >
                    <span className="truncate font-mono">{r.case_id}</span>
                    <span
                      className={`num sev-fg-${severity(r.base?.overall ?? null)}`}
                      title="base mean"
                    >
                      {fmtScore(r.base?.overall ?? null)}
                    </span>
                    <span
                      className={`num sev-fg-${severity(r.compare?.overall ?? null)}`}
                      title="compare mean"
                    >
                      {fmtScore(r.compare?.overall ?? null)}
                    </span>
                    <span className={`num ${deltaTone(r.delta)}`}>
                      {fmtDelta(r.delta) ?? "—"}
                    </span>
                    <Badge variant="secondary" className="justify-self-end">
                      {CHANGE_LABEL[r.change]}
                    </Badge>
                  </button>
                  {open.has(r.case_id) && (
                    <CaseDetail
                      row={r}
                      data={data}
                      base={base}
                      compare={compare}
                    />
                  )}
                </div>
              ))}
            </div>
          </Card>
        )}

        {view === "grid" && (
          <>
            <ScoreMatrix
              id="compare-cases"
              report={data.compare}
              groups={scoreGroups(data.compare)}
              rows={shown.map((r) => ({
                case_id: r.case_id,
                row: r.compare,
                base: r.base,
              }))}
              filtered={only !== null}
              withBase
              caseCell={({ case_id, row }) =>
                row ? (
                  <Link
                    className="text-inherit"
                    to={`/experiments/${encodeURIComponent(compare)}/${encodeURIComponent(case_id)}`}
                    target="_blank"
                    rel="noopener"
                  >
                    {case_id}
                  </Link>
                ) : (
                  case_id
                )
              }
            />
          </>
        )}
      </div>
    </>
  );
}
