import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import {
  elapsedMs,
  fmtScore,
  fmtWhen,
  formatElapsed,
  formatUsd,
  normalizeMathDelimiters,
  severity,
  verdictKind,
  verdictWord,
} from "../lib/format";
import type { Verdict } from "../lib/format";
import {
  detailShape,
  isScalar,
  looksLikeRate,
  nonEmptyKeys,
} from "../lib/detailShape";
import { groupEvaluators, groupForConfig } from "../lib/evaluatorGroups";
import type { EvaluatorGroup } from "../lib/evaluatorGroups";
import {
  RUBRIC_COLUMNS,
  RUBRIC_SUMMARY_KEYS,
  isRubricSummary,
  joinRubricItems,
  rubricRows,
} from "../lib/rubricSummary";
import type { RubricRow } from "../lib/rubricSummary";
import type {
  CaseFile,
  CaseScorePoint,
  EvalScore,
  LintFinding,
} from "../lib/types";
import { CodeBlock, CopyButton, InfoTip } from "../lib/ui";
import { toYaml } from "../lib/format";
import { downloadFile, serverNow } from "../lib/api";
import { fixtureEntries } from "../lib/fixtures";
import { httpUrl } from "../lib/safeUrl";

export function Markdown({ text }: { text: string }) {
  const normalized = useMemo(() => normalizeMathDelimiters(text), [text]);
  return (
    <div className="md-body text-meta">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
      >
        {normalized}
      </ReactMarkdown>
    </div>
  );
}

export function PreBlock({
  text,
  language = "json",
}: {
  text: string;
  language?: string;
}) {
  return (
    <div className="group relative overflow-hidden rounded border border-line">
      {/* On hover only: at rest the button would sit on top of the first line
          of code it copies. */}
      <div className="absolute top-1 right-4 z-10 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <CopyButton text={text} />
      </div>
      <CodeBlock value={text} language={language} />
    </div>
  );
}

export function Tag({ children }: { children: React.ReactNode }) {
  return <span className="chip muted">{children}</span>;
}

// The files a case attaches to its question; each downloads on click. Sits
// beside the question, so it always shows its heading and says when there is
// nothing attached rather than leaving the column blank.
export function FixtureList({
  files,
}: {
  files: CaseFile[] | null | undefined;
}) {
  const entries = fixtureEntries(files);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <div className="eyebrow">Files</div>
      {error && <div className="pane-status error">{error}</div>}
      {entries.length === 0 ? (
        <div className="text-micro text-ink-4">No files attached.</div>
      ) : (
        <div className="card rows">
          {entries.map((entry) => (
            <button
              key={entry.key}
              type="button"
              className="row w-full grid-cols-[minmax(0,1fr)_auto] text-left"
              title={entry.context || `download ${entry.name}`}
              onClick={() =>
                downloadFile(entry.path).catch((e) =>
                  setError(
                    `download failed: ${e instanceof Error ? e.message : e}`,
                  ),
                )
              }
            >
              <span className="truncate font-mono text-meta text-brand">
                {entry.name}
              </span>
              <span className="row-meta">download ↓</span>
            </button>
          ))}
        </div>
      )}
    </>
  );
}

export function WorkspaceChip({
  workspace,
}: {
  workspace: string | null | undefined;
}) {
  if (!workspace) return null;
  return (
    <span className="chip" title="the experiment's home workspace">
      {workspace}
    </span>
  );
}

export function ScoreChip({
  name,
  score,
  compact = false,
  onClick,
}: {
  name: string;
  score: number | null;
  compact?: boolean;
  onClick?: () => void;
}) {
  const sev = severity(score);
  const label = compact ? fmtScore(score) : `${name} ${fmtScore(score)}`;
  const cls = `chip chip-${sev} num`;
  if (onClick) {
    return (
      <button
        type="button"
        className={cls}
        title={`${name}: ${fmtScore(score)} — view`}
        onClick={(e) => {
          e.stopPropagation();
          onClick();
        }}
      >
        {label}
      </button>
    );
  }
  return (
    <span className={cls} title={`${name}: ${fmtScore(score)}`}>
      {label}
    </span>
  );
}

// A single bucket score in a table cell: the number, tone-coloured, right
// aligned under its heading. "—" when the experiment carries no score for it.
export function ScoreCell({ score }: { score: number | null | undefined }) {
  const value = score ?? null;
  return (
    <span className={`num text-right sev-fg-${severity(value)}`}>
      {fmtScore(value)}
    </span>
  );
}

// A cost, right aligned and neutral (a cost is not graded, so it carries no
// tone).
export function CostCell({ cost }: { cost: number | null | undefined }) {
  return (
    <span className="num text-right text-ink-3">
      {cost === null || cost === undefined ? "—" : formatUsd(cost)}
    </span>
  );
}

// A small tone-coloured square holding one experiment's bucket score, with the
// experiment / agent / model on hover. Used in a strip of recent experiments.
export function ScoreBlock({
  score,
  title,
}: {
  score: number | null;
  title: string;
}) {
  const sev = severity(score);
  return (
    <span
      className={`num inline-flex h-5 w-7 items-center justify-center rounded text-micro sev-fg-${sev} ${
        score === null ? "border border-line-soft" : `heat-${sev}`
      }`}
      title={title}
    >
      {fmtScore(score)}
    </span>
  );
}

// One bucket's recent scores across the last N experiments (newest first), as
// a row of ScoreBlocks. "—" when the example has never been run.
export function ScoreBlockStrip({
  points,
  bucket,
}: {
  points: CaseScorePoint[] | undefined;
  bucket: string;
}) {
  if (!points || points.length === 0)
    return <span className="text-ink-4">—</span>;
  return (
    <span className="flex gap-0.5">
      {points.map((p) => (
        <ScoreBlock
          key={p.experiment_id}
          score={p.scores[bucket] ?? null}
          title={[`#${p.number}`, p.agent, p.model].filter(Boolean).join(" · ")}
        />
      ))}
    </span>
  );
}

// The harness git revision an experiment ran at. Renders nothing without a
// sha. `compact` (list cell) shows just the short sha; otherwise a chip reading
// "branch @ shortsha". Links to the commit when the agent's repo is known.
export function RevisionChip({
  sha,
  branch,
  url,
  compact = false,
}: {
  sha: string | null | undefined;
  branch?: string | null;
  url?: string | null;
  compact?: boolean;
}) {
  if (!sha) return null;
  const short = sha.slice(0, 8);
  const text = compact ? short : branch ? `${branch} @ ${short}` : short;
  const title = branch ? `${branch} @ ${sha}` : sha;
  const cls = compact
    ? "num font-mono text-meta text-ink-3 transition-colors hover:text-brand"
    : "chip muted font-mono";
  const href = httpUrl(url);
  if (href) {
    return (
      <a
        className={cls}
        href={href}
        target="_blank"
        rel="noreferrer"
        title={title}
        onClick={(e) => e.stopPropagation()}
      >
        {text}
      </a>
    );
  }
  return (
    <span className={cls} title={title}>
      {text}
    </span>
  );
}

export function UserPill({ user }: { user: string | null | undefined }) {
  if (!user) return null;
  return (
    <span
      className="chip muted inline-flex shrink-0 items-center gap-1"
      title="run by"
    >
      <svg
        width="11"
        height="11"
        viewBox="0 0 16 16"
        fill="currentColor"
        aria-hidden="true"
      >
        <path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6Zm-5.784 6A5.986 5.986 0 0 1 8 9.5c2.55 0 4.77 1.59 5.784 4.5a.75.75 0 0 1-.703 1H2.919a.75.75 0 0 1-.703-1Z" />
      </svg>
      {user}
    </span>
  );
}

export function LintBadges({
  findings,
}: {
  findings: LintFinding[] | undefined;
}) {
  if (!findings?.length) return null;
  return (
    <span className="inline-flex gap-1">
      {findings.map((f, i) => (
        <span
          key={i}
          className={`chip ${f.severity === "error" ? "chip-fail" : "chip-partial"}`}
          title={`${f.code}: ${f.message}`}
        >
          {f.code}
        </span>
      ))}
    </span>
  );
}

type Dict = Record<string, unknown>;

function asDict(v: unknown): Dict {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Dict) : {};
}

// ── ScoreDetail (per-item verdict tables) ─────────────────────────────────
const GLYPH: Record<Verdict, string> = {
  satisfied: "✓",
  partial: "◐",
  absent: "○",
  incorrect: "✗",
  other: "·",
};

const VERDICT_KEYS = new Set(["verdict", "status"]);

function cellText(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (isScalar(v)) return String(v);
  return toYaml(v).trimEnd();
}

function HeadCell({
  children = null,
  className = "",
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <th
      className={`border-b border-line-soft py-1 pr-2 text-left font-medium tracking-wide text-n-400 uppercase ${className}`}
    >
      {children}
    </th>
  );
}

// A judge quoting a whole markdown table back as the "stated" value would push
// the rows either side of it off the screen; capping the height keeps the table
// scannable and the long quote reachable.
const LONG_CELL = 240;

function Cell({ value }: { value: unknown }) {
  // A judge can nest a whole sub-verdict inside an item field (the citation
  // check rides along in `source`); the shape router draws it as a row of
  // stats rather than a wall of yaml.
  if (!isScalar(value)) {
    return (
      <div className="max-h-28 overflow-y-auto">
        <DetailValue value={value} />
      </div>
    );
  }
  const text = cellText(value);
  const body = (
    <span
      className={`break-words whitespace-pre-wrap ${typeof value === "number" ? "num" : ""}`}
    >
      {text}
    </span>
  );
  return text.length > LONG_CELL ? (
    <div className="max-h-28 overflow-y-auto">{body}</div>
  ) : (
    body
  );
}

// The row's own ordinal already numbers it, so an `index` column that just
// counts 1..n is a column of noise.
function isOrdinal(items: Record<string, unknown>[], key: string): boolean {
  return key === "index" && items.every((item, i) => item[key] === i + 1);
}

// What was asked of the answer leads — the type, then the rubric (or
// presentation item) text, then `expected` — so everything after it (stated,
// source, the judge's note) reads as the response to it.
const LEAD_KEYS = ["type", "rubric", "text", "expected"];

function ItemTable({ items }: { items: Record<string, unknown>[] }) {
  const lead = (k: string) => {
    const i = LEAD_KEYS.indexOf(k);
    return i === -1 ? LEAD_KEYS.length : i;
  };
  const keys = nonEmptyKeys(items)
    .filter((k) => !VERDICT_KEYS.has(k) && !isOrdinal(items, k))
    .sort((a, b) => lead(a) - lead(b));
  const hasWord = items.some((i) => verdictWord(i.verdict ?? i.status));
  return (
    <table className="w-full text-micro">
      <thead>
        <tr>
          <HeadCell className="w-6">#</HeadCell>
          <HeadCell className="w-5" />
          {hasWord ? <HeadCell className="w-20">verdict</HeadCell> : null}
          {keys.map((k) => (
            <HeadCell key={k}>{k}</HeadCell>
          ))}
        </tr>
      </thead>
      <tbody>
        {items.map((item, i) => {
          const verdictRaw = item.verdict ?? item.status;
          const kind = verdictKind(verdictRaw);
          return (
            <tr key={i} className={`iv-${kind} border-b border-n-50 align-top`}>
              <td className="num py-1 text-n-400">{i + 1}</td>
              <td className="py-1">{GLYPH[kind]}</td>
              {hasWord ? (
                <td className="py-1">{verdictWord(verdictRaw) ?? ""}</td>
              ) : null}
              {keys.map((k) => (
                <td key={k} className="py-1 pr-3">
                  <Cell value={item[k]} />
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// A rate shares the 0-1 scale of a score, so it reads on that scale — two
// decimals, and the score's colour. A count is a count: no decimals, and no
// colour, which would imply a threshold that doesn't exist.
function StatValue({ label, value }: { label: string; value: unknown }) {
  if (!looksLikeRate(label, value)) {
    return (
      <span className="num">{value === null ? "—" : cellText(value)}</span>
    );
  }
  return (
    <span className={`num sev-fg-${severity(value as number)}`}>
      {fmtScore(value as number)}
    </span>
  );
}

function Stat({ label, value }: { label: string; value: unknown }) {
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className="text-n-400">{label}</span>
      <StatValue label={label} value={value} />
    </span>
  );
}

function StatRow({ entries }: { entries: [string, unknown][] }) {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-micro">
      {entries.map(([k, v]) => (
        <Stat key={k} label={k} value={v} />
      ))}
    </div>
  );
}

function MatrixTable({
  rows,
}: {
  rows: Record<string, Record<string, unknown>>;
}) {
  const entries = Object.entries(rows);
  const keys = nonEmptyKeys(entries.map(([, r]) => r));
  return (
    <table className="w-full text-micro">
      <thead>
        <tr>
          <HeadCell />
          {keys.map((k) => (
            <HeadCell key={k} className="!text-right">
              {k}
            </HeadCell>
          ))}
        </tr>
      </thead>
      <tbody>
        {entries.map(([label, row]) => (
          <tr key={label} className="border-b border-n-50">
            <td className="py-1 pr-2">{label}</td>
            {keys.map((k) => (
              <td key={k} className="py-1 pr-2 text-right">
                <StatValue label={k} value={row[k]} />
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ObjectListTable({ rows }: { rows: Record<string, unknown>[] }) {
  const keys = nonEmptyKeys(rows);
  return (
    <table className="w-full text-micro">
      <thead>
        <tr>
          {keys.map((k) => (
            <HeadCell key={k}>{k}</HeadCell>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i} className="border-b border-n-50 align-top">
            {keys.map((k) => (
              <td
                key={k}
                className={`py-1 pr-2 break-words whitespace-pre-wrap ${
                  typeof row[k] === "number" ? "num" : ""
                }`}
              >
                {cellText(row[k])}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// `detail` is free-form JSON, so the form is chosen from the value's shape
// rather than from the evaluator's name — that keeps one renderer honest for
// every evaluator instead of a branch per family.
function DetailValue({ value }: { value: unknown }) {
  // Prose stays prose. A multi-line string is not a scalar — it can't sit in a
  // stat row — but putting a code editor around a judge's sentences is what
  // made these cards unreadable in the first place.
  if (typeof value === "string") {
    return <span className="break-words whitespace-pre-wrap">{value}</span>;
  }
  switch (detailShape(value)) {
    case "scalar":
      return <span className="whitespace-pre-wrap">{cellText(value)}</span>;
    case "scalar-map":
      return (
        <StatRow entries={Object.entries(value as Record<string, unknown>)} />
      );
    case "matrix":
      return (
        <MatrixTable rows={value as Record<string, Record<string, unknown>>} />
      );
    case "object-list":
      return <ObjectListTable rows={value as Record<string, unknown>[]} />;
    default:
      return <PreBlock text={toYaml(value)} language="yaml" />;
  }
}

// The per-item list is rendered as its own table, so the keys it arrives under
// are not repeated among the aggregates.
const PAYLOAD_KEYS = new Set(["items", "steps", "verdicts"]);

function KeyedBlocks({ entries }: { entries: [string, unknown][] }) {
  const flat = entries.filter(([, v]) => isScalar(v));
  const nested = entries.filter(([, v]) => !isScalar(v));
  if (!entries.length) return null;
  return (
    <div className="mt-1 space-y-2 text-micro">
      {flat.length ? <StatRow entries={flat} /> : null}
      {nested.map(([k, v]) => (
        <div key={k}>
          <div className="mb-0.5 font-mono text-n-400">{k}</div>
          <DetailValue value={v} />
        </div>
      ))}
    </div>
  );
}

function RubricSummaryRow({ row }: { row: RubricRow }) {
  return (
    <tr
      className={`border-b border-n-50 ${row.kind === "type" ? "text-ink-2" : ""}`}
      title={row.counts ?? undefined}
    >
      <td className={`py-1 pr-2 ${row.kind === "type" ? "pl-3" : ""}`}>
        {row.label}
      </td>
      {RUBRIC_COLUMNS.map((col) => (
        <td key={col} className="py-1 pr-2 text-right">
          <StatValue label={col} value={row.cells[col] ?? null} />
        </td>
      ))}
    </tr>
  );
}

// The whole-answer and citation rows are the reading; the per-type rows are
// the drill-down, folded under a toggle until asked for.
function RubricSummaryTable({ detail }: { detail: Record<string, unknown> }) {
  const rows = rubricRows(detail);
  const typeRows = rows.filter((r) => r.kind === "type");
  const [showTypes, setShowTypes] = useState(false);
  return (
    <table className="w-full text-micro">
      <thead>
        <tr>
          <HeadCell />
          {RUBRIC_COLUMNS.map((col) => (
            <HeadCell key={col} className="!text-right">
              {col}
            </HeadCell>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows
          .filter((r) => r.kind === "all")
          .map((row) => (
            <RubricSummaryRow key={row.label} row={row} />
          ))}
        {typeRows.length > 0 && (
          <tr className="border-b border-n-50">
            <td colSpan={RUBRIC_COLUMNS.length + 1} className="py-0.5">
              <button
                type="button"
                className="btn btn-quiet btn-sm -ml-1.5 text-ink-3"
                aria-expanded={showTypes}
                onClick={() => setShowTypes((v) => !v)}
              >
                {showTypes ? "▾" : "▸"} by type ({typeRows.length})
              </button>
            </td>
          </tr>
        )}
        {showTypes &&
          typeRows.map((row) => <RubricSummaryRow key={row.label} row={row} />)}
        {rows
          .filter((r) => r.kind === "citation")
          .map((row) => (
            <RubricSummaryRow key={row.label} row={row} />
          ))}
      </tbody>
    </table>
  );
}

function DetailRows({ detail }: { detail: Record<string, unknown> }) {
  // The rubric judge's counts arrive three ways (overall, by_type, citation);
  // one table per slice replaces all three. Keys outside the summary still get
  // the generic treatment below it.
  if (isRubricSummary(detail)) {
    return (
      <>
        <RubricSummaryTable detail={detail} />
        <KeyedBlocks
          entries={Object.entries(detail).filter(
            ([k]) => !PAYLOAD_KEYS.has(k) && !RUBRIC_SUMMARY_KEYS.has(k),
          )}
        />
      </>
    );
  }
  return (
    <KeyedBlocks
      entries={Object.entries(detail).filter(([k]) => !PAYLOAD_KEYS.has(k))}
    />
  );
}

// A config gets the same shape-driven treatment as a result: a rubric list is a
// table of rubrics, which is what the case page shows when there is no run to
// score against. Anything irregular still falls back to yaml.
function ConfigBlock({ cfg }: { cfg: unknown }) {
  if (cfg && typeof cfg === "object" && !Array.isArray(cfg)) {
    return (
      <KeyedBlocks entries={Object.entries(cfg as Record<string, unknown>)} />
    );
  }
  return <DetailValue value={cfg} />;
}

export function SummaryChip({ summary }: { summary?: Record<string, number> }) {
  if (!summary) return null;
  return (
    <span className="num inline-flex gap-2 text-micro">
      {Object.entries(summary).map(([k, n]) => {
        const kind = verdictKind(k);
        const word = verdictWord(k);
        return (
          <span key={k} className={`iv-${kind}`}>
            {GLYPH[kind]} {n}
            {word ? ` ${word}` : ""}
          </span>
        );
      })}
    </span>
  );
}

// A judge that answers in JSON has its answer parsed into `detail`, so the raw
// explanation is the same content a second time — print it only when it says
// something the rendered detail doesn't.
function isRedundantExplanation(score: EvalScore): boolean {
  if (!score.explanation || !score.detail) return false;
  const text = score.explanation.trim();
  if (!text.startsWith("{") && !text.startsWith("[")) return false;
  try {
    return JSON.stringify(JSON.parse(text)) === JSON.stringify(score.detail);
  } catch {
    return false;
  }
}

export function ScoreDetail({
  score,
  cfg,
}: {
  score: EvalScore;
  cfg?: unknown;
}) {
  // Aggregates before the per-item list: the rates and counts summarize the
  // table underneath them, and reading them first frames it.
  return (
    <>
      {score.detail ? <DetailRows detail={score.detail} /> : null}
      {score.items ? (
        <div className="mt-2">
          <ItemTable items={joinRubricItems(score.items, cfg)} />
        </div>
      ) : null}
      {score.explanation && !isRedundantExplanation(score) ? (
        <div className="mt-1 whitespace-pre-wrap">{score.explanation}</div>
      ) : null}
    </>
  );
}

// ── EvaluatorScores (config × result cards) ───────────────────────────────

// One judge pass can publish several scores — `answer_rubric_*` publishes
// seven — and each arrives with the same payload attached. Rendering them as
// separate cards prints the same judgement seven times, so members of a group
// that carry identical payloads are drawn once, labelled with the members that
// share it.
function payloadKey(score: EvalScore): string {
  return JSON.stringify([
    score.items ?? null,
    score.detail ?? null,
    score.explanation ?? null,
  ]);
}

interface PayloadBlock {
  key: string;
  score: EvalScore;
  names: string[];
}

function payloadBlocks(
  members: { name: string; score: EvalScore }[],
): PayloadBlock[] {
  const blocks: PayloadBlock[] = [];
  for (const { name, score } of members) {
    if (!score.items && !score.detail && !score.explanation) continue;
    const key = payloadKey(score);
    const existing = blocks.find((b) => b.key === key);
    if (existing) existing.names.push(name);
    else blocks.push({ key, score, names: [name] });
  }
  return blocks;
}

function MemberScore({
  name,
  title,
  score,
}: {
  name: string;
  title: string;
  score: number | null;
}) {
  return (
    <span
      className={`chip chip-${severity(score)} num inline-flex items-center gap-1`}
      title={`${title}: ${fmtScore(score)}`}
    >
      {name} {fmtScore(score)}
      <InfoTip term={title} />
    </span>
  );
}

// The member's own word, with the group's shared prefix taken off: inside an
// `answer_rubric` card the chips read "score / coverage / accuracy", not the
// prefix seven times over.
function shortName(name: string, title: string): string {
  return name.startsWith(`${title}_`) ? name.slice(title.length + 1) : name;
}

function EvaluatorGroupCard({
  group,
  cfg,
  scores,
}: {
  group: EvaluatorGroup;
  cfg: unknown;
  scores: Record<string, EvalScore>;
}) {
  const members = group.names
    .filter((n) => scores[n])
    .map((n) => ({ name: n, score: scores[n] }));
  const hasCfg = cfg !== undefined && cfg !== null;
  const blocks = payloadBlocks(members);
  const single = members.length === 1 ? members[0].score : null;
  const label = members.find((m) => m.score.label)?.score.label ?? null;
  const summary = members.find((m) => m.score.summary)?.score.summary;

  return (
    <div
      className="mb-2 rounded-lg border border-n-100 bg-n-0"
      data-eval={group.names.join(" ") || group.title}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-n-50 px-3 py-1.5 text-meta">
        <span className="font-mono font-medium">{group.title}</span>
        <InfoTip term={group.title} />
        {label ? <span className="text-n-400">· {label}</span> : null}
        <SummaryChip summary={summary} />
        <span className="flex-1" />
        {members.length === 0 ? (
          <span className="text-micro text-n-400">not scored</span>
        ) : single ? (
          <span className={`num sev-fg-${severity(single.score)}`}>
            {fmtScore(single.score)}
          </span>
        ) : (
          members.map((m) => (
            <MemberScore
              key={m.name}
              name={shortName(m.name, group.title)}
              title={m.name}
              score={m.score.score}
            />
          ))
        )}
      </div>
      <div className="space-y-2 px-3 py-2 text-micro">
        {blocks.map((block) => (
          <div key={block.key}>
            {blocks.length > 1 ||
            (members.length > 1 && block.names.length < members.length) ? (
              <div className="mb-1 font-mono text-n-400">
                {block.names.map((n) => shortName(n, group.title)).join(", ")}
              </div>
            ) : null}
            <ScoreDetail score={block.score} cfg={cfg} />
          </div>
        ))}
        {hasCfg ? (
          blocks.length ? (
            <details>
              <summary className="cursor-pointer text-n-400">config</summary>
              <ConfigBlock cfg={cfg} />
            </details>
          ) : (
            <ConfigBlock cfg={cfg} />
          )
        ) : null}
      </div>
    </div>
  );
}

const OTHER = "other scores";

export function EvaluatorScores({
  evaluators,
  scores,
}: {
  evaluators: unknown;
  scores: Record<string, EvalScore>;
}) {
  const configs: { name: string; family: string; cfg: unknown }[] = [];
  for (const [family, fam] of Object.entries(asDict(evaluators))) {
    for (const [name, cfg] of Object.entries(asDict(fam))) {
      configs.push({ name, family, cfg });
    }
  }

  const groups = groupEvaluators(Object.keys(scores));
  const claimed = new Set<string>();
  const matchedByGroup = new Map<
    EvaluatorGroup,
    { name: string; family: string; cfg: unknown }[]
  >();
  for (const c of configs) {
    const group = groupForConfig(c.name, groups);
    if (!group) continue;
    claimed.add(c.name);
    matchedByGroup.set(group, [...(matchedByGroup.get(group) ?? []), c]);
  }

  const cards = groups.map((group) => {
    const matched = matchedByGroup.get(group) ?? [];
    return {
      key: group.names.join("|"),
      group,
      family: matched[0]?.family ?? OTHER,
      cfg:
        matched.length === 0
          ? undefined
          : matched.length === 1
            ? matched[0].cfg
            : Object.fromEntries(matched.map((m) => [m.name, m.cfg])),
    };
  });
  // A configured evaluator that published nothing still belongs on the page —
  // "not scored" is the finding.
  const unscored = configs
    .filter((c) => !claimed.has(c.name))
    .map((c) => ({
      key: c.name,
      group: { title: c.name, names: [] as string[] },
      family: c.family,
      cfg: c.cfg,
    }));

  const all = [...cards, ...unscored];
  if (!all.length) return null;
  const familyOrder = [...Object.keys(asDict(evaluators)), OTHER];

  return (
    <>
      {familyOrder
        .filter((family) => all.some((c) => c.family === family))
        .map((family) => (
          <div key={family}>
            <div className="mt-3 mb-1.5 flex items-center gap-2 text-meta font-semibold text-ink-2">
              {family}
              <span className="h-px flex-1 bg-line-soft" aria-hidden />
            </div>
            {all
              .filter((c) => c.family === family)
              .map((c) => (
                <EvaluatorGroupCard
                  key={c.key}
                  group={c.group}
                  cfg={c.cfg}
                  scores={scores}
                />
              ))}
          </div>
        ))}
      <details className="mt-1 text-micro">
        <summary className="cursor-pointer text-n-400">
          Full evaluator config
        </summary>
        <PreBlock text={toYaml(evaluators)} language="yaml" />
      </details>
    </>
  );
}

export function TimeSpan({
  start,
  end,
  running = false,
  doneLabel = "took",
  doneTitle,
}: {
  start: string | null | undefined;
  end: string | null | undefined;
  running?: boolean;
  doneLabel?: string;
  doneTitle?: string;
}) {
  const [now, setNow] = useState(serverNow);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(serverNow()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  const started = fmtWhen(start);
  if (!started) return null;
  const ended = fmtWhen(end);
  const live = running && !end;
  const took = live
    ? Math.max(0, now - new Date(start!).getTime())
    : elapsedMs(start, end);
  return (
    <span
      className="row-meta num"
      title={[
        `started ${started}`,
        ended && `ended ${ended}`,
        !live && doneTitle,
      ]
        .filter(Boolean)
        .join(" · ")}
    >
      {started}
      {took !== null &&
        ` · ${live ? "running" : doneLabel} ${formatElapsed(took)}`}
    </span>
  );
}
