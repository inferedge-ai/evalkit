import { stringify as yamlStringify } from "yaml";

// Eval configs are authored in YAML; presenting structured values as YAML is
// more faithful (and more readable) than JSON.stringify of the parsed object.
export function toYaml(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return yamlStringify(value, { lineWidth: 100 });
}

export type Severity = "pass" | "partial" | "fail" | "none";

const PASS = 0.8;
const FAIL = 0.5;

export function severity(score: number | null | undefined): Severity {
  if (score === null || score === undefined) return "none";
  if (score >= PASS) return "pass";
  if (score >= FAIL) return "partial";
  return "fail";
}

export function fmtScore(score: number | null): string {
  return score === null || score === undefined ? "—" : score.toFixed(2);
}

export function stringify(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

export function fmtGauge(name: string, value: number | null): string {
  if (value === null || value === undefined) return "—";
  if (name.includes("cost_usd")) return `$${value.toFixed(4)}`;
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function fmtDelta(delta: number | null | undefined): string | null {
  if (delta === null || delta === undefined) return null;
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "±";
  return `${sign}${Math.abs(delta).toFixed(2)}`;
}

// The answer-rubric scores in report order — the whole-answer rates, then the
// per-type breakdown. Mirrors ANSWER_RUBRIC_ORDER in core/scorecard.py, which
// orders the same names in the scorecard's evaluator_columns. Alphabetical
// order interleaves the two groups, which reads as seven unrelated scores.
const ANSWER_RUBRIC_ORDER = [
  "answer_rubric_score",
  "answer_rubric_coverage",
  "answer_rubric_accuracy",
  "answer_rubric_citation",
  "answer_rubric_factual_retrieval",
  "answer_rubric_derived_computation",
  "answer_rubric_analysis_interpretation",
];

export function orderEvaluatorNames(names: string[]): string[] {
  const rank = (n: string) => {
    const i = ANSWER_RUBRIC_ORDER.indexOf(n);
    return i === -1 ? ANSWER_RUBRIC_ORDER.length : i;
  };
  return [...names].sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

export type Verdict =
  "satisfied" | "partial" | "absent" | "incorrect" | "other";

// Absent means the answer never addressed the rubric — a gap, warn-toned.
// Incorrect means it addressed it and got it wrong — an error, red.
const SATISFIED = new Set(["satisfied", "pass", "hit", "respected"]);
const ABSENT = new Set(["missing", "absent", "omitted"]);
const INCORRECT = new Set([
  "incorrect",
  "unsatisfied",
  "fail",
  "wrong",
  "violated",
]);
const CANONICAL = new Set(["satisfied", "partial", "missing"]);

export function verdictKind(v: unknown): Verdict {
  if (v === true || (typeof v === "string" && SATISFIED.has(v)))
    return "satisfied";
  if (v === "partial") return "partial";
  if (typeof v === "string" && ABSENT.has(v)) return "absent";
  if (v === false || (typeof v === "string" && INCORRECT.has(v)))
    return "incorrect";
  return "other";
}

// A vocabulary word beyond the canonical three (hit/wrong/omitted/…) carries
// meaning the glyph alone loses — surface it next to the glyph.
export function verdictWord(v: unknown): string | null {
  return typeof v === "string" && !CANONICAL.has(v) ? v : null;
}

// CLDR only defines an alphabetic abbreviation for a zone in the locales that
// use it (IST exists in en-IN, EST in en-US), so try both before settling for
// the GMT offset.
function zoneLabel(d: Date, timeZone: string): string {
  let fallback = "";
  for (const locale of ["en-US", "en-IN"]) {
    const label =
      new Intl.DateTimeFormat(locale, { timeZone, timeZoneName: "short" })
        .formatToParts(d)
        .find((p) => p.type === "timeZoneName")?.value ?? "";
    if (label && !label.startsWith("GMT")) return label;
    if (!fallback) fallback = label;
  }
  return fallback;
}

export function fmtWhen(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d.getTime())) return null;
  const timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const formatted = new Intl.DateTimeFormat("en-GB", {
    timeZone,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
  const label = zoneLabel(d, timeZone);
  return label ? `${formatted} ${label}` : formatted;
}

// Agents emit TeX-style \( \) / \[ \] delimiters; remark-math only reads
// dollar delimiters. Math comes only from those TeX delimiters, so escape
// every raw $ first — otherwise prose like "$16.6bn ... $42.2bn" reads as
// inline math. Then translate display blocks before inline (so an inline pass
// can never split one), inserting the only unescaped $. Backtick spans/fences
// stay verbatim.
export function normalizeMathDelimiters(text: string): string {
  return text
    .split(/(`{3,}[\s\S]*?`{3,}|`[^`\n]*`)/)
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part
            .replace(/\$/g, "\\$")
            .replace(/\\\[(.+?)\\\]/gs, (_, tex: string) => `$$${tex}$$`)
            .replace(/\\\((.+?)\\\)/gs, (_, tex: string) => `$${tex}$`),
    )
    .join("");
}

export function outputText(output: unknown): string | null {
  if (output === null || output === undefined) return null;
  return JSON.stringify(output, null, 2);
}

export function answerText(answer: unknown): string {
  if (answer === null || answer === undefined)
    return "No run for this case yet.";
  if (typeof answer === "string") return answer;
  if (typeof answer === "object") {
    const rec = answer as Record<string, unknown>;
    for (const key of ["response", "answer", "text", "output", "content"]) {
      if (typeof rec[key] === "string") return rec[key] as string;
    }
  }
  return JSON.stringify(answer, null, 2);
}

export function questionText(input: unknown): string {
  if (input && typeof input === "object") {
    const rec = input as Record<string, unknown>;
    for (const key of ["question", "query", "prompt", "input"]) {
      if (typeof rec[key] === "string") return rec[key] as string;
    }
  }
  return stringify(input);
}

// An experiment name is "<dataset>-<agent>-<stamp>". On a dataset's own page
// the dataset prefix is the one part the reader already knows, so split it off.
export function experimentLabel(
  name: string | null | undefined,
  datasetName: string | null | undefined,
): { variant: string; stamp: string | null } {
  if (!name) return { variant: "", stamp: null };
  const stripped =
    datasetName && name.startsWith(`${datasetName}-`)
      ? name.slice(datasetName.length + 1)
      : name;
  const colon = stripped.indexOf(":");
  if (colon === -1) return { variant: stripped, stamp: null };
  return {
    variant: stripped.slice(0, colon) || stripped,
    stamp: stripped.slice(colon + 1) || null,
  };
}

// "myagent · gemini-3.6-flash" / "support-bot" — the harness (+ model override) an
// experiment ran with, from the runner-stamped experiment metadata.
export function experimentTag(
  meta: { agent?: unknown; model?: unknown } | undefined,
): string | null {
  if (!meta || typeof meta.agent !== "string" || !meta.agent) return null;
  const model =
    typeof meta.model === "string" && meta.model
      ? ` · ${meta.model.split("/").pop()}`
      : "";
  return `${meta.agent}${model}`;
}

// The dataset subset an experiment ran on, from its stored selection: a
// tag/split name, "all cases", or an ad-hoc case-id list.
export function splitLabel(
  meta: { selection?: unknown } | undefined,
): string | null {
  const sel = meta?.selection as
    { all?: boolean; tag?: string; case_ids?: string[] } | undefined;
  if (!sel) return null;
  if (typeof sel.tag === "string" && sel.tag) return sel.tag;
  if (Array.isArray(sel.case_ids) && sel.case_ids.length) {
    const n = sel.case_ids.length;
    return n === 1 ? sel.case_ids[0] : `${n} cases`;
  }
  if (sel.all) return "all cases";
  return null;
}

// ── Token / cost / size formatters ────────────────────────────────────────
export function formatUsd(n: number): string {
  if (n >= 100) return `$${n.toFixed(0)}`;
  if (n >= 1) return `$${n.toFixed(2)}`;
  return `$${n.toFixed(4)}`;
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(1)}B`;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatTokensFull(n: number): string {
  return n.toLocaleString("en-US");
}

export function formatBytes(n: number): string {
  if (n >= 1_073_741_824) return `${(n / 1_073_741_824).toFixed(1)} GB`;
  if (n >= 1_048_576) return `${(n / 1_048_576).toFixed(1)} MB`;
  if (n >= 1_024) return `${(n / 1_024).toFixed(1)} kB`;
  return `${n} B`;
}

export function formatMs(ms: number): string {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`;
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(1)}s`;
  return `${Math.round(ms)}ms`;
}

export function formatElapsed(ms: number): string {
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m${s % 60}s`;
  return `${Math.floor(s / 3600)}h${Math.floor((s % 3600) / 60)}m`;
}

export function elapsedMs(
  start: string | null | undefined,
  end: string | null | undefined,
): number | null {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return Number.isFinite(ms) && ms >= 0 ? ms : null;
}

export function splitTags(metadata: {
  tags?: string[];
  overlays?: Record<string, { tags?: string[] }> | null;
}): string[] {
  const overlayTags = Object.values(metadata.overlays ?? {}).flatMap(
    (block) => block.tags ?? [],
  );
  return [...new Set([...(metadata.tags ?? []), ...overlayTags])];
}
