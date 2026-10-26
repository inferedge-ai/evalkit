import type { CaseRow, EvaluatorStat, Report } from "./types";

export type ColumnKind = "score" | "gauge" | "avg";

export interface BucketCard {
  bucket: string;
  score: number | null;
  members: EvaluatorStat[];
}

export interface ColumnGroup {
  label: string;
  kind: "score" | "gauge";
  cols: string[];
}

export const BY_TYPE = "by type";

const ANSWER_RUBRIC_RATES = [
  "answer_rubric_coverage",
  "answer_rubric_accuracy",
  "answer_rubric_citation",
];

const DIAGNOSTIC_GROUPS: { label: string; match: (c: string) => boolean }[] = [
  { label: "answer rubric", match: (c) => ANSWER_RUBRIC_RATES.includes(c) },
  { label: BY_TYPE, match: (c) => c.startsWith("answer_rubric_") },
  { label: "other", match: () => true },
];

export function mean(values: (number | null | undefined)[]): number | null {
  const present = values.filter((v): v is number => v != null);
  return present.length
    ? present.reduce((a, b) => a + b, 0) / present.length
    : null;
}

export function bucketCards(report: Report): BucketCard[] {
  return Object.entries(report.buckets)
    .filter(([, score]) => score !== null)
    .map(([bucket, score]) => ({
      bucket,
      score,
      members: report.evaluators.filter((e) => e.bucket === bucket),
    }));
}

export function scoreGroups(report: Report): ColumnGroup[] {
  const bucketOf = new Map(report.evaluators.map((e) => [e.name, e.bucket]));
  const groups: ColumnGroup[] = Object.keys(report.buckets)
    .map((bucket) => ({
      label: bucket,
      kind: "score" as const,
      cols: report.evaluator_columns.filter((c) => bucketOf.get(c) === bucket),
    }))
    .filter((g) => g.cols.length);
  const taken = new Set(groups.flatMap((g) => g.cols));
  for (const g of DIAGNOSTIC_GROUPS) {
    const cols = report.evaluator_columns.filter(
      (c) => !taken.has(c) && g.match(c),
    );
    cols.forEach((c) => taken.add(c));
    if (cols.length) groups.push({ label: g.label, kind: "score", cols });
  }
  return groups;
}

export function gaugeGroup(report: Report): ColumnGroup | null {
  const cols = report.gauges.filter((g) => g.count > 0).map((g) => g.name);
  return cols.length ? { label: "gauges", kind: "gauge", cols } : null;
}

export function cellValue(
  row: CaseRow,
  kind: ColumnKind,
  col: string,
): number | null {
  if (kind === "avg") return row.overall;
  return (kind === "gauge" ? row.gauges[col] : row.scores[col]) ?? null;
}

export function serverMean(
  report: Report,
  kind: ColumnKind,
  col: string,
): number | null {
  if (kind === "avg") return mean(report.cases.map((r) => r.overall));
  if (kind === "gauge")
    return report.gauges.find((g) => g.name === col)?.mean ?? null;
  return report.evaluators.find((e) => e.name === col)?.score ?? null;
}

export function visibleStat(
  rows: CaseRow[],
  kind: ColumnKind,
  col: string,
): { mean: number | null; n: number } {
  const values = rows
    .map((r) => cellValue(r, kind, col))
    .filter((v) => v !== null);
  return { mean: mean(values), n: values.length };
}

export function hasScores(report: Report): boolean {
  return report.evaluators.some((e) => e.score !== null);
}

export const CHANGE_THRESHOLD = 0.05;

export type Side = "both" | "base" | "compare";

export interface CompareMember {
  name: string;
  base: number | null;
  compare: number | null;
  side: Side;
}

export interface CompareCard {
  bucket: string;
  base: number | null;
  compare: number | null;
  members: CompareMember[];
}

export type CaseChange =
  | "improved"
  | "regressed"
  | "unchanged"
  | "unscored"
  | "only_in_base"
  | "only_in_compare";

export const CHANGE_ORDER: CaseChange[] = [
  "regressed",
  "improved",
  "unchanged",
  "unscored",
  "only_in_base",
  "only_in_compare",
];

export const CHANGE_LABEL: Record<CaseChange, string> = {
  improved: "improved",
  regressed: "regressed",
  unchanged: "unchanged",
  unscored: "unscored",
  only_in_base: "only in base",
  only_in_compare: "only in compare",
};

export interface CompareRow {
  case_id: string;
  base: CaseRow | null;
  compare: CaseRow | null;
  delta: number | null;
  change: CaseChange;
}

export function delta(
  base: number | null | undefined,
  compare: number | null | undefined,
): number | null {
  return base == null || compare == null
    ? null
    : Math.round((compare - base) * 1e6) / 1e6;
}

export function compareCards(base: Report, compare: Report): CompareCard[] {
  const buckets = [
    ...new Set([...Object.keys(base.buckets), ...Object.keys(compare.buckets)]),
  ];
  return buckets
    .map((bucket) => {
      const scores = (r: Report) =>
        new Map(
          r.evaluators
            .filter((e) => e.bucket === bucket && e.score !== null)
            .map((e) => [e.name, e.score]),
        );
      const before = scores(base);
      const after = scores(compare);
      const names = [...new Set([...before.keys(), ...after.keys()])];
      return {
        bucket,
        base: base.buckets[bucket] ?? null,
        compare: compare.buckets[bucket] ?? null,
        members: names.map((name) => ({
          name,
          base: before.get(name) ?? null,
          compare: after.get(name) ?? null,
          side: (before.has(name) && after.has(name)
            ? "both"
            : before.has(name)
              ? "base"
              : "compare") as Side,
        })),
      };
    })
    .filter((card) => card.base !== null || card.compare !== null);
}

export function classifyCase(
  base: CaseRow | undefined,
  compare: CaseRow | undefined,
): CaseChange {
  if (!compare) return "only_in_base";
  if (!base) return "only_in_compare";
  const d = delta(base.overall, compare.overall);
  if (d === null) return "unscored";
  if (d >= CHANGE_THRESHOLD) return "improved";
  if (d <= -CHANGE_THRESHOLD) return "regressed";
  return "unchanged";
}

export function compareRows(base: Report, compare: Report): CompareRow[] {
  const before = new Map(base.cases.map((r) => [r.case_id, r]));
  const after = new Map(compare.cases.map((r) => [r.case_id, r]));
  const ids = [...new Set([...before.keys(), ...after.keys()])];
  return ids
    .map((case_id) => {
      const b = before.get(case_id);
      const c = after.get(case_id);
      return {
        case_id,
        base: b ?? null,
        compare: c ?? null,
        delta: delta(b?.overall, c?.overall),
        change: classifyCase(b, c),
      };
    })
    .sort(
      (x, y) =>
        (x.delta ?? Infinity) - (y.delta ?? Infinity) ||
        CHANGE_ORDER.indexOf(x.change) - CHANGE_ORDER.indexOf(y.change) ||
        x.case_id.localeCompare(y.case_id),
    );
}

const PASSING = new Set([
  "satisfied",
  "met",
  "present",
  "correct",
  "pass",
  "passed",
  "yes",
]);
const SHOWN_CHECKS = 3;

function checks(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value))
    return value.flatMap((v) =>
      v && typeof v === "object" && "verdict" in v
        ? [v as Record<string, unknown>]
        : checks(v),
    );
  if (value && typeof value === "object")
    return Object.values(value).flatMap(checks);
  return [];
}

// Judge explanations are JSON or free text, and free text may start with
// "[" or "{" too; anything that does not parse is shown as written.
function parseJson(text: string): unknown {
  if (!/^\s*[[{]/.test(text)) return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

export function failedChecks(explanation: string): {
  lines: string[];
  more: number;
} {
  const parsed = parseJson(explanation);
  if (parsed === undefined) return { lines: [explanation], more: 0 };
  const failed = checks(parsed)
    .filter((c) => !PASSING.has(String(c.verdict).toLowerCase()))
    .map((c) => `${c.verdict}: ${c.rubric ?? c.text ?? c.note ?? ""}`);
  return {
    lines: failed.slice(0, SHOWN_CHECKS),
    more: Math.max(0, failed.length - SHOWN_CHECKS),
  };
}
