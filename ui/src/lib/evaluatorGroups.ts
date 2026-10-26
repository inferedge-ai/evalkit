import { orderEvaluatorNames } from "./format";

export interface EvaluatorGroup {
  /** What the card is titled: the shared prefix, or the lone evaluator's name. */
  title: string;
  names: string[];
}

// One evaluator can publish several scores — `answer_rubric_*` publishes seven,
// `value_*` five — and they arrive as separate names. Group them by the prefix
// the framework already names them with, so one judge pass reads as one card
// instead of seven near-identical ones.
//
// The key is the first two segments of a three-or-more-segment name, and the
// first segment otherwise. One segment alone is too coarse: it would file
// `tool_call_efficiency` and `tool_trajectory_match` — two unrelated
// evaluators — under a single "tool" card.
export function groupKey(name: string): string {
  const segments = name.split("_");
  return segments.slice(0, segments.length > 2 ? 2 : 1).join("_");
}

export function groupEvaluators(names: string[]): EvaluatorGroup[] {
  const buckets = new Map<string, string[]>();
  for (const name of names) {
    const key = groupKey(name);
    buckets.set(key, [...(buckets.get(key) ?? []), name]);
  }
  const groups: EvaluatorGroup[] = [];
  for (const [key, members] of buckets) {
    const ordered = orderEvaluatorNames(members);
    // A group of one is not a group: a lone `max_tool_calls` keeps its full
    // name rather than being retitled "max_tool".
    groups.push({
      title: members.length === 1 ? members[0] : sharedPrefix(ordered, key),
      names: ordered,
    });
  }
  return groups;
}

// The longest prefix every member shares, cut at a segment boundary so the
// title is a word rather than a fragment: answer_rubric_score + …_coverage →
// "answer_rubric", not "answer_rubric_".
function sharedPrefix(names: string[], fallback: string): string {
  const segments = names[0].split("_");
  let shared = 0;
  for (let i = 0; i < segments.length; i++) {
    const candidate = segments.slice(0, i + 1).join("_");
    if (names.every((n) => n === candidate || n.startsWith(`${candidate}_`))) {
      shared = i + 1;
    } else break;
  }
  return shared > 0 ? segments.slice(0, shared).join("_") : fallback;
}

/** How many leading `_`-segments two names share. */
function sharedSegments(a: string, b: string): number {
  const x = a.split("_");
  const y = b.split("_");
  let n = 0;
  while (n < x.length && n < y.length && x[n] === y[n]) n++;
  return n;
}

// A config key and the scores it publishes are named off the same stem, but not
// identically — `answer_rubrics` (config) publishes `answer_rubric_coverage`
// (score) — so a config is matched to the group whose title shares the most of
// its name, not by equality.
export function groupForConfig(
  configName: string,
  groups: EvaluatorGroup[],
): EvaluatorGroup | undefined {
  const candidates = groups.filter(
    (g) =>
      sharedSegments(g.title, configName) > 0 || g.names.includes(configName),
  );
  if (candidates.length === 0) return undefined;
  return candidates.sort(
    (a, b) =>
      sharedSegments(b.title, configName) - sharedSegments(a.title, configName),
  )[0];
}
