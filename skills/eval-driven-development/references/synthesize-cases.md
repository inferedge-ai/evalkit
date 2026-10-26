# Synthesize a case plan

Instructions for turning a batch of app-run observations into one ranked case
plan. Run once per batch — as a subagent where supported, else inline.
Inputs: the `observations/` directory (one JSON per run, from
`analyze-run.md`), the batch's `index.json`, the target suite directory (the
case YAML the new cases will join), and the path to write
(`<dir>/case-plan.md`).

## What you may touch

- **Read access to the entire repo and the suite directory.**
- **Writing is allowed for exactly one file: the case plan path you were
  given.** Don't write case YAML; M5 does that with the developer.

## Your job

1. Read every observation and `index.json`.
2. Cluster the `failed` and `degraded` observations into patterns. Two
   observations share a pattern when they have the same `task_type`, the same
   failure `kind`, and the evidence points at the same root cause (the same
   surface misbehaving the same way). Name each pattern in plain words.
3. Score each pattern:

   ```
   frequency = runs in the pattern / all observations
   impact    = highest weight among its failures:
               wrong_answer 4, refusal 3, missing_step 3, wrong_tool 2,
               inefficiency 1, presentation 1
   score     = frequency × impact
   fixed     = every failing run in the pattern has fixed_since set
   ```

4. Check coverage: read the suite's case YAML and find cases that already
   assert the pattern's ideal behaviour. Drop a pattern that is fully covered;
   keep a partly covered one and say which part no case asserts.
5. Rank by score, highest first. Within the same score, unfixed patterns come
   before fixed ones.
6. Propose 2–3 varied cases per pattern from the observations' case seeds.
7. Write the plan.

## Case plan format

```
# Case plan — <agent>, <since> to <until>

<N> runs analyzed: <f> failed, <d> degraded, <o> ok, <u> unclear. <P> patterns.

## Pattern 1 — <plain name>   [<k> of <N> runs · impact <i>]
Runs: <run_id>, <run_id>, …
Surface: <kind> <file or "not located">
Already covered by: none | <case id> (partly: <what is missing>)
Fixed since the runs: no | yes, <sha> "<what changed>" (expected green)
Proposed cases:
  - id: <kebab-id>   type: <task_type>   ground truth: known | NEEDS DEVELOPER
    input: <time-anchored input>
    asserts: <the ideal behaviour, one line>
Level: blackbox rubrics; overlay <check> if the check names harness tools

## Healthy
- <task_type>: no failures in <n> runs

## Unclear or low confidence
- <run_id>: <why>
```

## Rules for proposed cases

- Generalise: each case states the ideal behaviour for a new, time-anchored
  input. Never copy a user's run, words or data into a case; that overfits to
  one run and puts user data into a suite.
- Assert what correct output looks like for this input and every sibling like
  it, not only that today's failure is absent.
- `ground truth: NEEDS DEVELOPER` when the correct answer can't be checked
  from the runs' tool results or the agent's data sources. Never guess.
- Blackbox first. A check that names tools or skills belongs in an overlay
  (see the authoring skill's `references/overlays.md`).
- A fixed pattern still gets cases: they guard the fix. Mark it "expected
  green" with the commit.
- Unclear and low-confidence observations are listed, never clustered or
  turned into cases.

## EDD-infra note (optional, at the end of the plan)

If the batch showed a gap in the eval setup itself (runs missing data you
needed, a check evalkit should offer), add one line starting `EDD-infra
note:`. Otherwise omit it.
