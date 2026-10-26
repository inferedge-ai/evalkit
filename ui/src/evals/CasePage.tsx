import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, isNotFound, qs } from "../lib/api";
import { fmtWhen, questionText, toYaml } from "../lib/format";
import { BUCKET_COLUMNS, scoreValue } from "../lib/bucketColumns";
import { DataTable, useDataTable } from "../lib/DataTable";
import type { Column } from "../lib/DataTable";
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

const PAGE = 10;

const NO_POINTS: CaseScorePoint[] = [];

function ScoreHistory({
  dataset,
  caseId,
}: {
  dataset: string;
  caseId: string;
}) {
  const [points, setPoints] = useState<CaseScorePoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    setPoints(null);
    setError(null);
    api<CaseScoresResponse>(
      `/datasets/${encodeURIComponent(dataset)}/cases/${encodeURIComponent(caseId)}/scores${qs(
        { limit: String(PAGE), offset: String(offset) },
      )}`,
    )
      .then((r) => setPoints(r.points))
      .catch((e) => setError(String(e)));
  }, [dataset, caseId, offset]);

  const hasMore = (points?.length ?? 0) === PAGE;
  const buckets = BUCKET_COLUMNS;
  const columns = useMemo<Column<CaseScorePoint>[]>(
    () => [
      {
        id: "experiment",
        header: "experiment",
        accessorKey: "number",
        cell: (c) => {
          const p = c.row.original;
          return (
            <Link
              to={`/experiments/${p.experiment_id}`}
              className="font-mono font-medium text-brand hover:underline"
              title={`#${p.number} by ${p.created_by} · ${fmtWhen(p.created_at) ?? ""}${
                p.repetitions > 1 ? ` · ×${p.repetitions}` : ""
              }`}
            >
              #{p.number}
            </Link>
          );
        },
      },
      {
        id: "agent",
        header: "agent",
        accessorKey: "agent",
        meta: { wrap: true },
        cell: (c) => <span className="row-meta">{c.row.original.agent}</span>,
      },
      {
        id: "model",
        header: "model",
        accessorFn: (p) => p.model ?? "",
        meta: { wrap: true },
        cell: (c) => (
          <span className="row-meta">{c.row.original.model ?? "—"}</span>
        ),
      },
      ...buckets.map((column): Column<CaseScorePoint> => ({
        id: `score:${column.bucket}`,
        header: column.short,
        accessorFn: (p) => scoreValue(p.scores, column.bucket) ?? undefined,
        sortUndefined: "last",
        meta: { align: "right", title: `${column.bucket} bucket` },
        cell: (c) => <ScoreCell score={c.row.original.scores[column.bucket]} />,
      })),
      {
        id: "cost",
        header: "cost",
        accessorFn: (p) => p.cost_usd ?? undefined,
        sortUndefined: "last",
        meta: { align: "right" },
        cell: (c) => <CostCell cost={c.row.original.cost_usd} />,
      },
    ],
    [buckets],
  );
  const table = useDataTable({
    id: "case-score-history",
    data: points ?? NO_POINTS,
    columns,
    getRowId: (p) => String(p.experiment_id),
  });

  return (
    <>
      <div className="eyebrow">Score history</div>
      {error && <div className="pane-status error">{error}</div>}
      <DataTable
        table={table}
        loading={!points && !error}
        skeletonRows={4}
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
  const [example, setExample] = useState<CaseView | null>(null);
  const [missing, setMissing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    setMissing(null);
    api<CaseView>(
      `/datasets/${encodeURIComponent(datasetId)}/cases/${encodeURIComponent(exampleId)}`,
    )
      .then(setExample)
      .catch((e) => {
        if (!isNotFound(e)) return setError(String(e));
        setMissing(String(e).includes("unknown case") ? "case" : "dataset");
      });
  }, [datasetId, exampleId, version]);

  const reload = () => setVersion((v) => v + 1);

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
    <div className="page">
      <div className="toolbar mb-1">
        <nav className="flex min-w-0 items-baseline gap-1.5">
          <Link
            className="shrink-0 text-meta text-ink-3 transition-colors hover:text-brand"
            to={`/datasets/${encodeURIComponent(datasetId)}/cases`}
          >
            {datasetId}
          </Link>
          <span className="text-ink-4" aria-hidden>
            ›
          </span>
          <h1 className="page-title font-mono">
            <CopyText value={example.case_id} />
          </h1>
        </nav>
      </div>
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
              onChanged={reload}
            />
            <CaseChangelog
              dataset={datasetId}
              caseId={example.case_id}
              currentHash={example.content_hash}
              version={version}
            />
            <CaseComments
              dataset={datasetId}
              caseId={example.case_id}
              currentHash={example.content_hash}
              version={version}
            />
          </div>
        ) : null}
      </div>
    </div>
  );
}
