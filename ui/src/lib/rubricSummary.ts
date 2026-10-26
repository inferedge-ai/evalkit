// The answer_rubric judge reports the same counts three ways — overall
// scalars, a by_type map, and a citation object. One table with a row per
// slice reads them as one measurement instead of three.

type Dict = Record<string, unknown>;

function asDict(v: unknown): Dict {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Dict) : {};
}

// The three rates are the table; the raw counts behind them ride along as a
// hover title so the table stays three numbers wide.
export const RUBRIC_COLUMNS = ["score", "coverage", "accuracy"] as const;

const COUNT_KEYS = ["satisfied", "incorrect", "absent", "attempted"] as const;

// Keys the consolidated table consumes; anything else in the detail still
// renders through the generic blocks underneath it.
export const RUBRIC_SUMMARY_KEYS: Set<string> = new Set([
  ...RUBRIC_COLUMNS,
  ...COUNT_KEYS,
  "total",
  "recall",
  "rate",
  "eligible",
  "by_type",
  "citation",
]);

export type RubricRowKind = "all" | "type" | "citation";

export interface RubricRow {
  label: string;
  // Type rows are the drill-down; the table shows them only on request.
  kind: RubricRowKind;
  cells: Partial<Record<(typeof RUBRIC_COLUMNS)[number], unknown>>;
  counts: string | null;
}

// Explanations written before the server carried rubric/type on each verdict
// have neither key; the evaluator config's ordered rubrics list is the same
// list the judge was prompted with, so verdict `index` N joins to
// rubrics[N-1] — the join the server now does, replayed client-side.
export function joinRubricItems(items: Dict[], cfg: unknown): Dict[] {
  const rubrics = configRubrics(cfg);
  if (rubrics.length === 0) return items;
  return items.map((item, i) => {
    const hasRubric =
      item.rubric !== undefined && item.rubric !== null && item.rubric !== "";
    const hasType =
      item.type !== undefined && item.type !== null && item.type !== "";
    if (hasRubric && hasType) return item;
    const index = typeof item.index === "number" ? item.index : i + 1;
    const rubric = rubrics[index - 1];
    if (!rubric) return item;
    return {
      ...item,
      ...(hasRubric ? {} : { rubric: rubric.text }),
      ...(hasType || rubric.type === null ? {} : { type: rubric.type }),
    };
  });
}

function configRubrics(
  cfg: unknown,
): { text: string | null; type: string | null }[] {
  const dict = asDict(cfg);
  const list = Array.isArray(dict.rubrics)
    ? dict.rubrics
    : // A grouped card's cfg is a map of member name → config; any member's
      // rubrics list is the shared one.
      (Object.values(dict)
        .map((v) => asDict(v).rubrics)
        .find(Array.isArray) ?? []);
  return list.map((entry: unknown) => {
    if (typeof entry === "string") return { text: entry, type: null };
    const e = asDict(entry);
    return {
      text:
        typeof e.text === "string"
          ? e.text
          : typeof e.rubric === "string"
            ? e.rubric
            : null,
      type: typeof e.type === "string" ? e.type : null,
    };
  });
}

export function isRubricSummary(detail: Dict): boolean {
  return (
    typeof detail.total === "number" &&
    typeof detail.satisfied === "number" &&
    (Object.keys(asDict(detail.by_type)).length > 0 ||
      Object.keys(asDict(detail.citation)).length > 0)
  );
}

function sliceCells(vals: Dict, score: unknown): RubricRow["cells"] {
  return { score, coverage: vals.coverage, accuracy: vals.accuracy };
}

// "17/20 satisfied · 2 incorrect · 1 absent · 19 attempted" — every count the
// judge reported for the slice, or null when it reported none.
export function sliceCounts(vals: Dict, total: unknown): string | null {
  const parts: string[] = [];
  for (const key of COUNT_KEYS) {
    const n = vals[key];
    if (typeof n !== "number") continue;
    parts.push(
      key === "satisfied" && typeof total === "number"
        ? `${n}/${total} satisfied`
        : `${n} ${key}`,
    );
  }
  return parts.length ? parts.join(" · ") : null;
}

// SCORE is the recall of the slice (satisfied/total — the number the header
// chips show); the citation row scores on its rate, and counts eligible items
// as its total.
export function rubricRows(detail: Dict): RubricRow[] {
  const rows: RubricRow[] = [
    {
      label: "all",
      kind: "all",
      cells: sliceCells(detail, detail.recall),
      counts: sliceCounts(detail, detail.total),
    },
  ];
  for (const [type, vals] of Object.entries(asDict(detail.by_type))) {
    const slice = asDict(vals);
    rows.push({
      label: type,
      kind: "type",
      cells: sliceCells(slice, slice.recall),
      counts: sliceCounts(slice, slice.total),
    });
  }
  const citation = asDict(detail.citation);
  if (Object.keys(citation).length > 0) {
    rows.push({
      label: "citation",
      kind: "citation",
      cells: sliceCells(citation, citation.rate),
      counts: sliceCounts(citation, citation.eligible),
    });
  }
  return rows;
}
