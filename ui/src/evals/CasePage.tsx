import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { ColDef } from "ag-grid-community";
import { isNotFound, qs } from "../lib/api";
import { fmtWhen, questionText, toYaml } from "../lib/format";
import { refreshQueries, useApi } from "../lib/query";
import { BUCKET_COLUMNS, scoreValue } from "../lib/bucketColumns";
import {
  ColumnsMenu,
  DataGrid,
  compareNullsLast,
  readValue,
  renderCell,
  useDataGrid,
} from "../lib/DataGrid";
import type {
  CaseScorePoint,
  CaseScoresResponse,
  CaseView,
} from "../lib/types";
import { CopyText } from "../lib/ui";
import { NotFound } from "../shell/NotFound";
import { CaseChangelog } from "./CaseChangelog";
import { CaseComments } from "./CaseComments";
import { ReviewPanel } from "./ReviewPanel";
import {
  CostCell,
  EvaluatorScores,
  FixtureList,
  Markdown,
  PreBlock,
  ScoreCell,
  Tag,
} from "./bits";
import { splitRef } from "../lib/workspace";
import { useFollowWorkspace, useWorkspace } from "../shell/workspace";
import { promotionSide, twinLine } from "../lib/promotion";
import { PageHeader } from "@/components/widgets/PageHeader";

const PAGE = 10;

const NO_POINTS: CaseScorePoint[] = [];

const getExperimentId = (p: CaseScorePoint) => String(p.experiment_id);

const SCORE_HISTORY_COLUMNS: ColDef<CaseScorePoint>[] = [
  {
    colId: "experiment",
    headerName: "experiment",
    field: "number",
    initialFlex: 0,
    initialWidth: 110,
    cellRenderer: renderCell<CaseScorePoint>((p) => (
      <Link
        to={`/experiments/${p.experiment_id}`}
        className="font-mono font-medium text-brand hover:underline"
        title={`#${p.number} by ${p.created_by} · ${fmtWhen(p.created_at) ?? ""}${
          p.repetitions > 1 ? ` · ×${p.repetitions}` : ""
        }`}
      >
        #{p.number}
      </Link>
    )),
  },
  {
    colId: "agent",
    headerName: "agent",
    type: "text",
    field: "agent",
    cellRenderer: renderCell<CaseScorePoint>((p) => (
      <span className="row-meta" title={p.agent}>
        {p.agent}
      </span>
    )),
  },
  {
    colId: "model",
    headerName: "model",
    type: "text",
    valueGetter: readValue((p) => p.model ?? ""),
    cellRenderer: renderCell<CaseScorePoint>((p) => (
      <span className="row-meta" title={p.model ?? undefined}>
        {p.model ?? "—"}
      </span>
    )),
  },
  ...BUCKET_COLUMNS.map((column): ColDef<CaseScorePoint> => ({
    colId: `score:${column.bucket}`,
    headerName: column.short,
    headerTooltip: `${column.bucket} bucket`,
    type: "number",
    valueGetter: readValue((p) => scoreValue(p.scores, column.bucket)),
    comparator: compareNullsLast,
    cellRenderer: renderCell<CaseScorePoint>((p) => (
      <ScoreCell score={p.scores[column.bucket]} />
    )),
  })),
  {
    colId: "cost",
    headerName: "cost",
    type: "number",
    valueGetter: readValue((p) => p.cost_usd),
    comparator: compareNullsLast,
    cellRenderer: renderCell<CaseScorePoint>((p) => (
      <CostCell cost={p.cost_usd} />
    )),
  },
];

function ScoreHistory({
  dataset,
  caseId,
}: {
  dataset: string;
  caseId: string;
}) {
  const [offset, setOffset] = useState(0);
  const scores = useApi<CaseScoresResponse>(
    `/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(caseId)}/scores${qs(
      { limit: String(PAGE), offset: String(offset) },
    )}`,
  );
  const points = scores.data?.points ?? null;
  const error = scores.error ? String(scores.error) : null;

  const hasMore = (points?.length ?? 0) === PAGE;
  const grid = useDataGrid<CaseScorePoint>("case-score-history");

  return (
    <>
      <div className="eyebrow">
        Score history
        <span className="ml-auto font-normal tracking-normal normal-case">
          <ColumnsMenu grid={grid} />
        </span>
      </div>
      {error && <div className="pane-status error">{error}</div>}
      <DataGrid
        grid={grid}
        rows={points ?? NO_POINTS}
        columns={SCORE_HISTORY_COLUMNS}
        getRowId={getExperimentId}
        loading={!points && !error}
        empty={offset === 0 ? "This case has never been run." : undefined}
      />
      {(offset > 0 || hasMore) && (
        <div className="toolbar mt-2">
          <button
            className="btn btn-sm"
            disabled={offset === 0}
            onClick={() => setOffset(Math.max(0, offset - PAGE))}
          >
            ← Newer
          </button>
          <span className="row-meta">
            {points && points.length > 0
              ? `${offset + 1}–${offset + points.length}`
              : "—"}
          </span>
          <button
            className="btn btn-sm"
            disabled={!hasMore}
            onClick={() => setOffset(offset + PAGE)}
          >
            Older →
          </button>
        </div>
      )}
    </>
  );
}

export function CasePage() {
  const { ref: datasetId = "", caseId: exampleId = "" } = useParams();
  const workspaceName = splitRef(datasetId).workspace;
  useFollowWorkspace(workspaceName);
  const { workspaces } = useWorkspace();
  const side = promotionSide(workspaces.find((w) => w.name === workspaceName));
  const loaded = useApi<CaseView>(
    `/datasets/${encodeURIComponent(datasetId)}/cases/${encodeURIComponent(exampleId)}`,
  );
  const example = loaded.data ?? null;
  const notFound = isNotFound(loaded.error);
  const missing = notFound
    ? String(loaded.error).includes("unknown case")
      ? "case"
      : "dataset"
    : null;
  const error = loaded.error && !notFound ? String(loaded.error) : null;

  if (missing === "dataset")
    return (
      <NotFound
        what={`dataset ${datasetId}`}
        up={{ label: "All datasets", to: "/datasets" }}
      />
    );
  if (missing === "case")
    return (
      <NotFound
        what={`case ${exampleId} in ${datasetId}`}
        up={{
          label: datasetId,
          to: `/datasets/${encodeURIComponent(datasetId)}/cases`,
        }}
      />
    );
  if (error) return <div className="pane-status error">{error}</div>;
  if (!example) return <div className="pane-status">Loading case…</div>;

  const tags = example.split_tags;
  const twin = example.twin && side ? example.twin : null;
  const twinPanel =
    twin && side
      ? {
          href: twin.href,
          text:
            side === "target"
              ? `Review this case in ${splitRef(datasetId).name}@${twin.workspace}`
              : twinLine(twin, side),
          locked: side === "target",
        }
      : null;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: "Datasets", to: "/datasets" },
          {
            label: datasetId,
            to: `/datasets/${encodeURIComponent(datasetId)}/cases`,
          },
        ]}
        title={
          <span className="font-mono">
            <CopyText value={example.case_id} />
          </span>
        }
      />
      <div className="page">
        {tags.length > 0 ? (
          <div className="mb-3 flex flex-wrap items-center gap-1">
            {tags.map((tag) => (
              <Tag key={tag}>{tag}</Tag>
            ))}
          </div>
        ) : null}

        <div className="grid gap-x-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,1fr)]">
          <div className="min-w-0">
            <div className="eyebrow">Question</div>
            <div className="card p-4">
              <Markdown text={questionText(example.input)} />
            </div>
            <FixtureList files={example.files} />
            <details className="mt-2 text-micro">
              <summary className="cursor-pointer text-n-400">Raw input</summary>
              <PreBlock text={toYaml(example.input)} language="yaml" />
            </details>

            {example.expected ? (
              <>
                <div className="eyebrow">Expected</div>
                <PreBlock text={toYaml(example.expected)} language="yaml" />
              </>
            ) : null}

            <div className="eyebrow">Evaluators</div>
            <EvaluatorScores evaluators={example.evaluators} scores={{}} />
            <details className="mt-2 text-micro">
              <summary className="cursor-pointer text-n-400">
                Full evaluator config
              </summary>
              <PreBlock text={toYaml(example.evaluators)} language="yaml" />
            </details>

            <ScoreHistory dataset={datasetId} caseId={example.case_id} />
          </div>

          {example.review ? (
            <div className="min-w-0">
              <ReviewPanel
                dataset={datasetId}
                caseId={example.case_id}
                review={example.review}
                twin={twinPanel}
                onChanged={() => void refreshQueries()}
              />
              <CaseChangelog
                dataset={datasetId}
                caseId={example.case_id}
                currentHash={example.content_hash}
              />
              <CaseComments
                dataset={datasetId}
                caseId={example.case_id}
                currentHash={example.content_hash}
              />
            </div>
          ) : null}
        </div>
      </div>
    </>
  );
}
