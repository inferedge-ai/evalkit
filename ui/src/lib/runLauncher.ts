import type { CaseView, JudgeSettings } from "./types";

export type Scope = "all" | "split" | "file" | "cases";

export interface ScopeChoice {
  scope: Scope;
  tag: string;
  file: string;
  caseIds: string[];
}

export type Selection =
  { all: true } | { tag: string } | { case_ids: string[] };

export interface LaunchRequest {
  dataset: string;
  agent: string;
  repetitions: number;
  selection: Selection;
  model: string | null;
  include_drafts: boolean;
  judge: JudgeSettings | null;
}

export function sourceFiles(
  examples: CaseView[],
): { name: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const ex of examples) {
    const source = ex.source;
    if (source) counts.set(source, (counts.get(source) ?? 0) + 1);
  }
  return [...counts]
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

export function casesInScope(
  examples: CaseView[],
  choice: ScopeChoice,
): CaseView[] {
  switch (choice.scope) {
    case "all":
      return examples;
    case "split":
      return examples.filter((ex) => ex.split_tags.includes(choice.tag));
    case "file":
      return examples.filter((ex) => ex.source === choice.file);
    case "cases": {
      const wanted = new Set(choice.caseIds);
      return examples.filter((ex) => wanted.has(ex.case_id));
    }
  }
}

export function selectionFor(
  examples: CaseView[],
  choice: ScopeChoice,
): Selection | null {
  if (choice.scope === "all") return { all: true };
  if (choice.scope === "split") return choice.tag ? { tag: choice.tag } : null;
  const ids = casesInScope(examples, choice).map((ex) => ex.case_id);
  return ids.length ? { case_ids: ids } : null;
}

export function approvedCount(examples: CaseView[]): number {
  return examples.filter((ex) => ex.review?.state === "approved").length;
}

const quote = (value: string) =>
  /^[\w./:@=+-]+$/.test(value) ? value : `'${value.replaceAll("'", "'\\''")}'`;

export function cliCommand(req: LaunchRequest): string {
  const target =
    "tag" in req.selection
      ? `${req.dataset}/${req.selection.tag}`
      : req.dataset;
  const parts = ["evalkit run", quote(target), `--agent ${quote(req.agent)}`];
  if (req.repetitions !== 1) parts.push(`--runs ${req.repetitions}`);
  if ("case_ids" in req.selection)
    parts.push(...req.selection.case_ids.map((id) => `--case-id ${quote(id)}`));
  if (req.model) parts.push(`--model ${quote(req.model)}`);
  if (req.include_drafts) parts.push("--include-drafts");
  if (req.judge)
    parts.push(
      `--judge-model ${quote(req.judge.model)}`,
      `--judge-base-url ${quote(req.judge.base_url)}`,
      `--judge-effort ${quote(req.judge.reasoning_effort)}`,
    );
  return parts.join(" ");
}
