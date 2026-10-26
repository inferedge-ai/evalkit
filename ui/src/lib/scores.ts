// Judge-backed evaluators pack their structured verdicts into the score row's
// `explanation` as JSON (counts lead, `items`/`verdicts` ride last). Parse that
// back out so the cards can render tables instead of JSON strings.

import { verdictKind } from "./format";
import type { EvalScore, EvaluationRow } from "./types";

const PAYLOAD_KEYS = ["items", "steps", "verdicts"];

export function toEvalScore(row: EvaluationRow): EvalScore {
  const score: EvalScore = {
    score: row.value,
    label: row.label,
    explanation: row.error ? `error: ${row.error}` : row.explanation,
  };
  const text = row.explanation?.trim();
  if (!text || (!text.startsWith("{") && !text.startsWith("["))) return score;
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return score;
  }
  if (Array.isArray(parsed)) {
    if (parsed.every((v) => v && typeof v === "object")) {
      score.items = parsed as Record<string, unknown>[];
      score.explanation = null;
    }
    return score;
  }
  if (parsed && typeof parsed === "object") {
    const record = parsed as Record<string, unknown>;
    for (const key of PAYLOAD_KEYS) {
      const value = record[key];
      if (
        Array.isArray(value) &&
        value.every((v) => v && typeof v === "object")
      ) {
        score.items = value as Record<string, unknown>[];
        break;
      }
    }
    const detail = Object.fromEntries(
      Object.entries(record).filter(([k]) => !PAYLOAD_KEYS.includes(k)),
    );
    if (Object.keys(detail).length) score.detail = detail;
    score.explanation = null;
    if (score.items) {
      const summary: Record<string, number> = {};
      for (const item of score.items) {
        const verdict = item.verdict ?? item.status;
        if (verdict === undefined) continue;
        const key =
          typeof verdict === "string" ? verdict : verdictKind(verdict);
        summary[key] = (summary[key] ?? 0) + 1;
      }
      if (Object.keys(summary).length) score.summary = summary;
    }
  }
  return score;
}

export function toEvalScores(rows: EvaluationRow[]): Record<string, EvalScore> {
  const out: Record<string, EvalScore> = {};
  for (const row of rows) {
    if (row.kind === "gauge") continue;
    out[row.name] = toEvalScore(row);
  }
  return out;
}

export function gaugeRows(rows: EvaluationRow[]): EvaluationRow[] {
  return rows.filter((row) => row.kind === "gauge");
}
