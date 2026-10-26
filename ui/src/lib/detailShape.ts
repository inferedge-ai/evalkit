// An evaluator's `detail` is free-form JSON, so the renderer picks a form from
// the value's shape rather than from the evaluator's name. Without this every
// non-string value went to a code editor, which put a full Monaco instance
// around `coverage: 1.0`.

export type Shape = "scalar" | "scalar-map" | "matrix" | "object-list" | "yaml";

export type Scalar = string | number | boolean | null;

export function isScalar(value: unknown): value is Scalar {
  if (value === null) return true;
  const t = typeof value;
  if (t === "number" || t === "boolean") return true;
  // A multi-line string is prose, not a value in a row.
  return t === "string" && !(value as string).includes("\n");
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === "object" && !Array.isArray(value);
}

function sameKeys(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((k, i) => k === b[i]);
}

export function detailShape(value: unknown): Shape {
  if (isScalar(value)) return "scalar";

  if (isPlainObject(value)) {
    const values = Object.values(value);
    if (values.length === 0) return "scalar-map";
    if (values.every(isScalar)) return "scalar-map";
    // A map of same-shaped maps is a table with the outer keys as its rows —
    // `by_type` is exactly that, and reads as one when drawn as one.
    if (values.every(isPlainObject)) {
      const keys = values.map((v) => Object.keys(v as object).sort());
      if (keys.every((k) => sameKeys(k, keys[0]))) return "matrix";
    }
    return "yaml";
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return "scalar-map";
    if (value.every(isPlainObject)) {
      const keys = value.map((v) => Object.keys(v as object).sort());
      if (keys.every((k) => sameKeys(k, keys[0]))) return "object-list";
    }
    return "yaml";
  }

  return "yaml";
}

/** Column order for a matrix / object-list: first appearance wins. */
export function unionKeys(rows: Record<string, unknown>[]): string[] {
  const keys: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) if (!keys.includes(key)) keys.push(key);
  }
  return keys;
}

/** Columns where at least one row has something to show. */
export function nonEmptyKeys(rows: Record<string, unknown>[]): string[] {
  return unionKeys(rows).filter((key) =>
    rows.some((row) => {
      const v = row[key];
      return v !== null && v !== undefined && v !== "";
    }),
  );
}

// A rate is reported on the same 0-1 scale as a score, so it can carry the same
// colour; a count cannot, and colouring it would imply a threshold that does
// not exist.
export function looksLikeRate(key: string, value: unknown): boolean {
  if (typeof value !== "number") return false;
  if (!Number.isFinite(value) || value < 0 || value > 1) return false;
  return !Number.isInteger(value) || value === 0 || value === 1
    ? RATE_KEYS.has(key)
    : false;
}

const RATE_KEYS = new Set([
  "coverage",
  "accuracy",
  "recall",
  "rate",
  "precision",
  "score",
  "ratio",
]);
