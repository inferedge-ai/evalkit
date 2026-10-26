import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, apiJson, qs } from "../lib/api";
import { questionText } from "../lib/format";
import { BUCKET_COLUMNS } from "../lib/bucketColumns";
import { ColumnsMenu, DataTable, useDataTable } from "../lib/DataTable";
import type { Column } from "../lib/DataTable";
import { REVIEW_STATES, stateClass, stateLabel } from "../lib/review";
import type {
  CaseScorePoint,
  CaseView,
  MemberRow,
  ReviewState,
} from "../lib/types";
import { ScoreBlockStrip } from "./bits";
import { Select } from "../lib/Select";
import { splitRef } from "../lib/workspace";
import {
  promotedImpliesApproved,
  promotionSide,
  twinCell,
} from "../lib/promotion";
import { useWorkspace } from "../shell/workspace";

// Last N experiments' bucket scores shown inline per case, newest first.
const RECENT = 4;

const NO_CASES: CaseView[] = [];
const HIDDEN = ["source"];

export function DatasetCasesTab({
  dataset,
  reviewed,
  onRun,
}: {
  dataset: string;
  reviewed: boolean;
  onRun: (caseIds: string[]) => void;
}) {
  const [params, setParams] = useSearchParams();
  const tag = params.get("tag") ?? "";
  const [q, setQ] = useState("");
  const [state, setState] = useState<ReviewState | "">("");
  const [examples, setExamples] = useState<CaseView[] | null>(null);
  const [scores, setScores] = useState<Record<string, CaseScorePoint[]>>({});
  const reviewable = reviewed;
  const { workspaces } = useWorkspace();
  const linked = workspaces.find((w) => w.name === splitRef(dataset).workspace);
  const side = promotionSide(linked);
  const requestable = reviewable && side !== "target";
  const otherWorkspace = linked?.promotes_to ?? linked?.promoted_from ?? null;
  const twinColumn = reviewable && side !== null && otherWorkspace !== null;
  const [reviewers, setReviewers] = useState<string[]>([]);
  const [reviewer, setReviewer] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const examplesPath = `/datasets/${encodeURIComponent(dataset)}/cases${qs({
    tag: tag || undefined,
  })}`;

  useEffect(() => {
    setExamples(null);
    api<CaseView[]>(examplesPath)
      .then(setExamples)
      .catch((e) => setError(String(e)));
  }, [dataset, tag, examplesPath]);

  useEffect(() => {
    setSelected(new Set());
  }, [dataset]);

  useEffect(() => {
    if (!requestable) return;
    const workspace = splitRef(dataset).workspace;
    api<MemberRow[]>(
      `/workspaces/${encodeURIComponent(workspace)}/members?role=reviewer`,
    )
      .then((rows) => {
        const names = rows.map((r) => r.username);
        setReviewers(names);
        setReviewer(names[0] ?? "");
      })
      .catch(() => setReviewers([]));
  }, [requestable, dataset]);

  // Recent scores are dataset-wide and tag-independent, so they load once and
  // are keyed by case id.
  useEffect(() => {
    setScores({});
    api<Record<string, CaseScorePoint[]>>(
      `/datasets/${encodeURIComponent(dataset)}/case-scores${qs({
        experiments: String(RECENT),
      })}`,
    )
      .then(setScores)
      .catch(() => setScores({}));
  }, [dataset]);

  const allTags = useMemo(
    () => [...new Set((examples ?? []).flatMap((ex) => ex.split_tags))].sort(),
    [examples],
  );

  const buckets = BUCKET_COLUMNS;

  const needle = q.trim().toLowerCase();
  const rows = useMemo(() => {
    const matched = needle
      ? (examples ?? NO_CASES).filter(
          (ex) =>
            ex.case_id.toLowerCase().includes(needle) ||
            questionText(ex.input).toLowerCase().includes(needle),
        )
      : (examples ?? NO_CASES);
    return state ? matched.filter((ex) => ex.review?.state === state) : matched;
  }, [examples, needle, state]);

  const columns = useMemo<Column<CaseView>[]>(
    () => [
      {
        id: "select",
        enableSorting: false,
        enableHiding: false,
        enableResizing: false,
        header: () => (
          <input
            type="checkbox"
            aria-label="select all shown"
            checked={
              rows.length > 0 && rows.every((ex) => selected.has(ex.case_id))
            }
            onChange={(e) =>
              setSelected(
                e.target.checked
                  ? new Set([...selected, ...rows.map((ex) => ex.case_id)])
                  : new Set(),
              )
            }
          />
        ),
        cell: (c) => (
          <input
            type="checkbox"
            aria-label={`select ${c.row.original.case_id}`}
            checked={selected.has(c.row.original.case_id)}
            onChange={(e) => toggle(c.row.original.case_id, e.target.checked)}
          />
        ),
      },
      {
        id: "id",
        header: "id",
        accessorKey: "case_id",
        meta: { wrap: true, className: "min-w-44 max-w-72" },
        cell: (c) => (
          <Link
            className="font-mono font-medium text-brand hover:underline"
            to={`/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(c.row.original.case_id)}`}
          >
            {c.row.original.case_id}
          </Link>
        ),
      },
      {
        id: "source",
        header: "source",
        accessorFn: (ex) => ex.source ?? "",
        cell: (c) =>
          c.row.original.source ? (
            <span className="row-meta font-mono">{c.row.original.source}</span>
          ) : (
            <span className="row-meta text-ink-4">—</span>
          ),
      },
      {
        id: "question",
        header: "question",
        accessorFn: (ex) => questionText(ex.input),
        meta: { wrap: true, className: "min-w-40" },
        cell: (c) => (
          <span
            className="line-clamp-2 text-ink-3"
            title={questionText(c.row.original.input)}
          >
            {questionText(c.row.original.input)}
          </span>
        ),
      },
      ...buckets.map((column): Column<CaseView> => ({
        id: `score:${column.bucket}`,
        header: column.label,
        enableSorting: false,
        meta: {
          title: `${column.bucket} bucket, last ${RECENT} experiments (newest first)`,
        },
        cell: (c) => (
          <ScoreBlockStrip
            points={(scores[c.row.original.case_id] ?? []).slice(0, RECENT)}
            bucket={column.bucket}
          />
        ),
      })),
      {
        id: "tags",
        header: "tags",
        accessorFn: (ex) => ex.split_tags.join(" "),
        meta: { wrap: true },
        cell: (c) => (
          <span className="flex gap-1">
            {c.row.original.split_tags.map((t) => (
              <span key={t} className="chip muted">
                {t}
              </span>
            ))}
          </span>
        ),
      },
      ...(twinColumn
        ? [
            {
              id: "twin",
              header: otherWorkspace ?? "",
              accessorFn: (ex) => twinCell(ex.twin, side)?.label ?? "",
              cell: (c) => {
                const cell = twinCell(c.row.original.twin, side);
                return cell ? (
                  <span className={`chip ${cell.cls}`}>{cell.label}</span>
                ) : null;
              },
            } satisfies Column<CaseView>,
          ]
        : []),
      ...(reviewable
        ? [
            {
              id: "state",
              header: "state",
              accessorFn: (ex) => ex.review?.state ?? "",
              cell: (c) => {
                const ex = c.row.original;
                return (
                  <span className="flex flex-col items-start gap-0.5">
                    {ex.review &&
                    !promotedImpliesApproved(ex.twin, side, ex.review.state) ? (
                      <span className={`chip ${stateClass(ex.review.state)}`}>
                        {stateLabel(ex.review.state)}
                      </span>
                    ) : null}
                    {ex.review?.state === "review_requested" &&
                    ex.review.requested_to ? (
                      <span className="row-meta">
                        → {ex.review.requested_to}
                      </span>
                    ) : null}
                  </span>
                );
              },
            } satisfies Column<CaseView>,
          ]
        : []),
    ],
    [
      buckets,
      rows,
      selected,
      scores,
      dataset,
      twinColumn,
      reviewable,
      side,
      otherWorkspace,
    ],
  );
  const table = useDataTable({
    id: "dataset-cases",
    data: rows,
    columns,
    getRowId: (ex) => ex.case_id,
    defaultHidden: HIDDEN,
  });

  const requestReview = async () => {
    setBusy(true);
    try {
      await apiJson(
        `/datasets/${encodeURIComponent(dataset)}/review-requests`,
        "POST",
        { selection: { case_ids: [...selected] }, reviewer },
      );
      setSelected(new Set());
      setExamples(await api<CaseView[]>(examplesPath));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (caseId: string, on: boolean) => {
    const next = new Set(selected);
    if (on) next.add(caseId);
    else next.delete(caseId);
    setSelected(next);
  };

  if (error) return <div className="pane-status error">{error}</div>;

  const loading = !examples;

  return (
    <>
      <div className="toolbar mb-2">
        <input
          className="input w-72"
          placeholder="Filter by id or question"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Select
          ariaLabel="tag"
          value={tag}
          onChange={(t) => setParams(t ? { tag: t } : {})}
          options={[
            { value: "", label: "All tags" },
            ...(tag && !allTags.includes(tag)
              ? [tag, ...allTags]
              : allTags
            ).map((t) => ({ value: t, label: t })),
          ]}
        />
        {reviewable && (
          <Select<ReviewState | "">
            ariaLabel="review state"
            value={state}
            onChange={setState}
            options={[
              { value: "", label: "All states" },
              ...REVIEW_STATES.map((s) => ({ value: s, label: stateLabel(s) })),
            ]}
          />
        )}
        <span className="row-meta num ml-auto">
          {loading ? "Loading…" : `${rows.length} cases`}
        </span>
        <ColumnsMenu table={table} defaultHidden={HIDDEN} />
      </div>
      {selected.size > 0 && (
        <div className="toolbar mb-2 rounded-md border border-brand bg-info-bg px-3 py-2">
          <span className="font-medium">{selected.size} selected</span>
          <button
            className="btn btn-sm btn-primary"
            onClick={() => onRun([...selected])}
          >
            Run selected
          </button>
          {requestable && (
            <>
              <span className="row-meta ml-2">Request review from</span>
              <Select
                ariaLabel="reviewer"
                value={reviewer}
                onChange={setReviewer}
                placeholder="No reviewers"
                options={reviewers.map((r) => ({ value: r, label: r }))}
              />
              <button
                className="btn btn-sm"
                disabled={busy || !reviewer}
                onClick={requestReview}
              >
                Request
              </button>
            </>
          )}
          <button
            className="btn btn-sm ml-auto"
            onClick={() => setSelected(new Set())}
          >
            ✕
          </button>
        </div>
      )}
      <DataTable
        table={table}
        loading={loading}
        empty="No cases match this filter."
      />
    </>
  );
}
