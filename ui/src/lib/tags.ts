import type { CaseView } from "./types";

export function hasAnyTag(caseTags: string[], wanted: string[]): boolean {
  return caseTags.some((t) => wanted.includes(t));
}

export function tagCounts(
  cases: CaseView[],
): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const c of cases)
    for (const t of c.split_tags) counts.set(t, (counts.get(t) ?? 0) + 1);
  return [...counts]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => a.name.localeCompare(b.name));
}
