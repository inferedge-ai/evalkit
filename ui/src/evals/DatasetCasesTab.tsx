import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { apiJson, qs } from "../lib/api";
import { questionText } from "../lib/format";
import { refreshQueries, useApi } from "../lib/query";
import type { ColDef } from "ag-grid-community";
import { BUCKET_COLUMNS } from "../lib/bucketColumns";
import {
  ColumnsMenu,
  DataGrid,
  readValue,
  renderCell,
  useDataGrid,
} from "../lib/DataGrid";
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
const NO_SCORES: Record<string, CaseScorePoint[]> = {};

const caseId = (ex: CaseView) => ex.case_id;

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
  const reviewable = reviewed;
  const { workspaces } = useWorkspace();
  const linked = workspaces.find((w) => w.name === splitRef(dataset).workspace);
  const side = promotionSide(linked);
  const requestable = reviewable && side !== "target";
  const otherWorkspace = linked?.promotes_to ?? linked?.promoted_from ?? null;
  const twinColumn = reviewable && side !== null && otherWorkspace !== null;
  const [pickedReviewer, setReviewer] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const cases = useApi<CaseView[]>(
    `/datasets/${encodeURIComponent(dataset)}/cases${qs({
      tag: tag || undefined,
    })}`,
  );
  const examples = cases.data ?? null;
  const error = actionError ?? (cases.error ? String(cases.error) : null);

  useEffect(() => {
    setSelected(new Set());
  }, [dataset]);

  const reviewers =
    useApi<MemberRow[]>(
      requestable
        ? `/workspaces/${encodeURIComponent(splitRef(dataset).workspace)}/members?role=reviewer`
        : null,
    ).data?.map((r) => r.username) ?? [];
  const reviewer = reviewers.includes(pickedReviewer)
    ? pickedReviewer
    : (reviewers[0] ?? "");

  // Recent scores are dataset-wide and tag-independent, so they load once and
  // are keyed by case id.
  const scores =
    useApi<Record<string, CaseScorePoint[]>>(
      `/datasets/${encodeURIComponent(dataset)}/case-scores${qs({
        experiments: String(RECENT),
      })}`,
    ).data ?? NO_SCORES;

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

  const columns = useMemo<ColDef<CaseView>[]>(
    () => [
      {
        colId: "select",
        lockPosition: "left",
        lockVisible: true,
        sortable: false,
        resizable: false,
        initialFlex: 0,
        initialWidth: 44,
        minWidth: 44,
        headerComponent: () => (
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
        cellRenderer: renderCell<CaseView>((ex) => (
          <input
            type="checkbox"
            aria-label={`select ${ex.case_id}`}
            checked={selected.has(ex.case_id)}
            onChange={(e) => toggle(ex.case_id, e.target.checked)}
          />
        )),
      },
      {
        colId: "id",
        headerName: "id",
        type: "text",
        field: "case_id",
        minWidth: 160,
        cellRenderer: renderCell<CaseView>((ex) => (
          <Link
            className="font-mono font-medium text-brand hover:underline"
            to={`/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(ex.case_id)}`}
            title={ex.case_id}
          >
            {ex.case_id}
          </Link>
        )),
      },
      {
        colId: "source",
        headerName: "source",
        initialHide: true,
        type: "text",
        valueGetter: readValue((ex) => ex.source ?? ""),
        cellRenderer: renderCell<CaseView>((ex) =>
          ex.source ? (
            <span className="row-meta font-mono">{ex.source}</span>
          ) : (
            <span className="row-meta text-ink-4">—</span>
          ),
        ),
      },
      {
        colId: "question",
        headerName: "question",
        type: "text",
        initialFlex: 2,
        valueGetter: readValue((ex) => questionText(ex.input)),
        cellRenderer: renderCell<CaseView>((ex) => (
          <span className="text-ink-3" title={questionText(ex.input)}>
            {questionText(ex.input)}
          </span>
        )),
      },
      ...buckets.map((column): ColDef<CaseView> => ({
        colId: `score:${column.bucket}`,
        headerName: column.label,
        headerTooltip: `${column.bucket} bucket, last ${RECENT} experiments (newest first)`,
        sortable: false,
        initialFlex: 0,
        initialWidth: 148,
        cellRenderer: renderCell<CaseView>((ex) => (
          <ScoreBlockStrip
            points={(scores[ex.case_id] ?? []).slice(0, RECENT)}
            bucket={column.bucket}
          />
        )),
      })),
      {
        colId: "tags",
        headerName: "tags",
        type: "text",
        valueGetter: readValue((ex) => ex.split_tags.join(" ")),
        cellRenderer: renderCell<CaseView>((ex) => (
          <span className="inline-flex gap-1">
            {ex.split_tags.map((t) => (
              <span key={t} className="chip muted">
                {t}
              </span>
            ))}
          </span>
        )),
      },
      ...(twinColumn
        ? [
            {
              colId: "twin",
              headerName: otherWorkspace ?? "",
              type: "text",
              valueGetter: readValue<CaseView, string>(
                (ex) => twinCell(ex.twin, side)?.label ?? "",
              ),
              cellRenderer: renderCell<CaseView>((ex) => {
                const cell = twinCell(ex.twin, side);
                return cell ? (
                  <span className={`chip ${cell.cls}`}>{cell.label}</span>
                ) : null;
              }),
            } satisfies ColDef<CaseView>,
          ]
        : []),
      ...(reviewable
        ? [
            {
              colId: "state",
              headerName: "state",
              type: "text",
              valueGetter: readValue<CaseView, string>(
                (ex) => ex.review?.state ?? "",
              ),
              cellRenderer: renderCell<CaseView>((ex) => (
                <span className="inline-flex items-center gap-1">
                  {ex.review &&
                  !promotedImpliesApproved(ex.twin, side, ex.review.state) ? (
                    <span className={`chip ${stateClass(ex.review.state)}`}>
                      {stateLabel(ex.review.state)}
                    </span>
                  ) : null}
                  {ex.review?.state === "review_requested" &&
                  ex.review.requested_to ? (
                    <span className="row-meta">→ {ex.review.requested_to}</span>
                  ) : null}
                </span>
              )),
            } satisfies ColDef<CaseView>,
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
  const grid = useDataGrid<CaseView>("dataset-cases");

  const requestReview = async () => {
    setBusy(true);
    try {
      await apiJson(
        `/datasets/${encodeURIComponent(dataset)}/review-requests`,
        "POST",
        { selection: { case_ids: [...selected] }, reviewer },
      );
      setSelected(new Set());
      await refreshQueries();
    } catch (e) {
      setActionError(String(e));
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
        <ColumnsMenu grid={grid} />
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
      <DataGrid
        grid={grid}
        rows={rows}
        columns={columns}
        getRowId={caseId}
        loading={loading}
        empty="No cases match this filter."
      />
    </>
  );
}
