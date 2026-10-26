import { describe, expect, it } from "vitest";
import {
  approvedCount,
  casesInScope,
  cliCommand,
  selectionFor,
  sourceFiles,
} from "./runLauncher";
import type { ScopeChoice } from "./runLauncher";
import type { CaseView } from "./types";

const ex = (
  case_id: string,
  source: string | undefined,
  tags: string[],
  state?: "approved" | "draft",
): CaseView =>
  ({
    case_id,
    source: source ?? null,
    split_tags: tags,
    input: {},
    expected: null,
    review: state ? { state, requested_to: null, updated_at: null } : null,
  }) as unknown as CaseView;

const examples = [
  ex("a", "tcs.yaml", ["tcs"], "approved"),
  ex("b", "tcs.yaml", ["tcs", "ratios"], "draft"),
  ex("c", "infy.yaml", ["ratios"], "approved"),
  ex("d", undefined, []),
];

const choice = (over: Partial<ScopeChoice>): ScopeChoice => ({
  scope: "all",
  tag: "",
  file: "",
  caseIds: [],
  ...over,
});

describe("sourceFiles", () => {
  it("counts cases per source file, sorted, skipping cases without one", () => {
    expect(sourceFiles(examples)).toEqual([
      { name: "infy.yaml", count: 1 },
      { name: "tcs.yaml", count: 2 },
    ]);
  });
});

describe("casesInScope / selectionFor", () => {
  it("all cases select everything", () => {
    expect(casesInScope(examples, choice({}))).toHaveLength(4);
    expect(selectionFor(examples, choice({}))).toEqual({ all: true });
  });

  it("a split sends the tag", () => {
    const c = choice({ scope: "split", tag: "ratios" });
    expect(casesInScope(examples, c).map((e) => e.case_id)).toEqual(["b", "c"]);
    expect(selectionFor(examples, c)).toEqual({ tag: "ratios" });
    expect(selectionFor(examples, choice({ scope: "split" }))).toBe(null);
  });

  it("a split counts cases tagged only by an overlay", () => {
    const overlaid = ex("d", "us.yaml", ["client-priority"]);
    const c = choice({ scope: "split", tag: "client-priority" });
    expect(
      casesInScope([...examples, overlaid], c).map((e) => e.case_id),
    ).toEqual(["d"]);
  });

  it("a case file expands into its case ids", () => {
    const c = choice({ scope: "file", file: "tcs.yaml" });
    expect(selectionFor(examples, c)).toEqual({ case_ids: ["a", "b"] });
  });

  it("picked cases keep dataset order and drop unknown ids", () => {
    const c = choice({ scope: "cases", caseIds: ["c", "zzz", "a"] });
    expect(selectionFor(examples, c)).toEqual({ case_ids: ["a", "c"] });
    expect(selectionFor(examples, choice({ scope: "cases" }))).toBe(null);
  });
});

describe("approvedCount", () => {
  it("counts approved cases only", () => {
    expect(approvedCount(examples)).toBe(2);
  });
});

describe("cliCommand", () => {
  it("names a shared workspace agent and the judge override", () => {
    expect(
      cliCommand({
        dataset: "finance@dev",
        agent: "ouragent@dev",
        repetitions: 1,
        selection: { all: true },
        model: null,
        include_drafts: false,
        judge: {
          base_url: "https://llm/v1",
          model: "other",
          reasoning_effort: "low",
          max_tokens: 1,
          concurrency: 1,
        },
      }),
    ).toBe(
      "evalkit run finance@dev --agent ouragent@dev --judge-model other --judge-base-url https://llm/v1 --judge-effort low",
    );
  });

  it("mirrors the request as evalkit run flags", () => {
    expect(
      cliCommand({
        dataset: "finance@dev",
        agent: "research-agent@dev",
        repetitions: 3,
        selection: { case_ids: ["a", "b c"] },
        model: "claude-opus-5-5",
        include_drafts: true,
        judge: null,
      }),
    ).toBe(
      "evalkit run finance@dev --agent research-agent@dev --runs 3 --case-id a --case-id 'b c' --model claude-opus-5-5 --include-drafts",
    );
  });

  it("omits defaults", () => {
    expect(
      cliCommand({
        dataset: "finance@dev",
        agent: "research-agent@dev",
        repetitions: 1,
        selection: { all: true },
        model: null,
        include_drafts: false,
        judge: null,
      }),
    ).toBe("evalkit run finance@dev --agent research-agent@dev");
  });

  it("puts a split into the path", () => {
    expect(
      cliCommand({
        dataset: "finance@dev",
        agent: "research-agent@dev",
        repetitions: 1,
        selection: { tag: "starbucks" },
        model: null,
        include_drafts: false,
        judge: null,
      }),
    ).toBe("evalkit run finance@dev/starbucks --agent research-agent@dev");
  });
});
