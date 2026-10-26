import type { CaseView, WorkspaceInfo } from "./types";
import { workspaceHint } from "./workspace";

export type SearchKind =
  "dataset" | "split" | "experiment" | "case" | "run" | "workspace";

export const SEARCH_KINDS: { kind: SearchKind; label: string; hint: string }[] =
  [
    { kind: "dataset", label: "Datasets", hint: "name@workspace" },
    { kind: "split", label: "Splits", hint: "name, across datasets" },
    {
      kind: "experiment",
      label: "Experiments",
      hint: "id, #number or dataset",
    },
    { kind: "case", label: "Cases", hint: "case id or question" },
    { kind: "run", label: "Runs", hint: "run id or title, per agent" },
    { kind: "workspace", label: "Workspaces", hint: "name, to switch to" },
  ];

export interface PaletteItem {
  key: string;
  label: string;
  sub?: string;
  tag: string;
  route: string;
  terms: string[];
  switchTo?: string;
}

export type LevelOneEntry =
  | { type: "kind"; kind: SearchKind; label: string; hint: string }
  | { type: "page"; label: string; route: string }
  | { type: "item"; item: PaletteItem };

export function splitItems(dataset: string, cases: CaseView[]): PaletteItem[] {
  const counts = new Map<string, number>();
  for (const c of cases)
    for (const tag of c.split_tags) counts.set(tag, (counts.get(tag) ?? 0) + 1);
  return [...counts].map(([tag, count]) => ({
    key: `split:${dataset}:${tag}`,
    label: tag,
    sub: `${dataset} · ${count} cases`,
    tag: "split",
    route: `/datasets/${encodeURIComponent(dataset)}/cases?tag=${encodeURIComponent(tag)}`,
    terms: [tag],
  }));
}

export function workspaceItems(
  workspaces: WorkspaceInfo[],
  current: string,
  me: string | null,
): PaletteItem[] {
  return workspaces.map((ws) => ({
    key: `workspace:${ws.name}`,
    label: `@${ws.name}`,
    sub:
      ws.name === current
        ? `${workspaceHint(ws, me)} · current`
        : workspaceHint(ws, me),
    tag: "workspace",
    route: "",
    terms: [ws.name, `@${ws.name}`],
    switchTo: ws.name,
  }));
}

function score(terms: string[], needle: string): number {
  let best = 0;
  for (const term of terms) {
    const t = term.toLowerCase();
    if (t === needle) return 3;
    if (t.startsWith(needle)) best = Math.max(best, 2);
    else if (t.includes(needle)) best = Math.max(best, 1);
  }
  return best;
}

export function rankItems(
  items: PaletteItem[],
  query: string,
  limit = 50,
): PaletteItem[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return items.slice(0, limit);
  return items
    .map((item, i) => ({ item, i, s: score(item.terms, needle) }))
    .filter((r) => r.s > 0)
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .slice(0, limit)
    .map((r) => r.item);
}

export function levelOne(
  query: string,
  pages: { to: string; label: string }[],
  items: PaletteItem[],
): LevelOneEntry[] {
  const needle = query.trim().toLowerCase();
  const kinds = SEARCH_KINDS.filter(
    (k) => !needle || k.label.toLowerCase().startsWith(needle),
  ).map((k): LevelOneEntry => ({ type: "kind", ...k }));
  const pageEntries = pages
    .filter((p) => !needle || p.label.toLowerCase().includes(needle))
    .map((p): LevelOneEntry => ({ type: "page", label: p.label, route: p.to }));
  const hits =
    needle.length >= 2
      ? rankItems(items, needle, 8).map((item): LevelOneEntry => ({
          type: "item",
          item,
        }))
      : [];
  return [...kinds, ...pageEntries, ...hits];
}
