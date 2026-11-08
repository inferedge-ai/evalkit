import type { ColumnState } from "ag-grid-community";

const STORAGE_PREFIX = "evalkit.table.";

export function loadColumnState(tableId: string): ColumnState[] {
  try {
    const saved: unknown = JSON.parse(
      localStorage.getItem(STORAGE_PREFIX + tableId) ?? "[]",
    );
    return Array.isArray(saved) &&
      saved.every((s) => typeof s?.colId === "string")
      ? saved
      : [];
  } catch {
    return [];
  }
}

export function saveColumnState(tableId: string, state: ColumnState[]) {
  localStorage.setItem(STORAGE_PREFIX + tableId, JSON.stringify(state));
}

// The saved layout for the grid's columns, in the saved order; a column added
// since slots in after the column it follows by default.
export function orderColumnState(
  saved: ColumnState[],
  columnIds: string[],
): ColumnState[] {
  const byId = new Map(saved.map((s) => [s.colId, s]));
  return mergeOrder(
    saved.map((s) => s.colId),
    columnIds,
  ).map((colId) => byId.get(colId) ?? { colId });
}

// The grid's current state, keeping what was saved for columns it doesn't
// show now (another experiment's evaluators) next to the column they followed.
// A sort on the current columns replaces one saved on an absent column.
export function mergeColumnState(
  saved: ColumnState[],
  current: ColumnState[],
): ColumnState[] {
  const currentIds = current.map((s) => s.colId);
  const savedIds = saved.map((s) => s.colId);
  const sorted = current.some((s) => s.sort);
  const absent = saved.map((s) =>
    sorted ? { ...s, sort: null, sortIndex: null } : s,
  );
  const byId = new Map([...absent, ...current].map((s) => [s.colId, s]));
  return mergeOrder(currentIds, [
    ...savedIds,
    ...currentIds.filter((colId) => !savedIds.includes(colId)),
  ]).map((colId) => byId.get(colId)!);
}

function mergeOrder(saved: string[], ids: string[]): string[] {
  const kept = saved.filter((id) => ids.includes(id));
  for (const [i, id] of ids.entries()) {
    if (kept.includes(id)) continue;
    const before = ids
      .slice(0, i)
      .reverse()
      .find((p) => kept.includes(p));
    kept.splice(before === undefined ? 0 : kept.indexOf(before) + 1, 0, id);
  }
  return kept;
}
