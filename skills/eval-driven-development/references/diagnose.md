# Diagnose one eval failure

Instructions for diagnosing **one** eval failure (one failing case) from its
pulled run directories and returning one structured JSON finding (root cause +
surface + evidence). On agents with subagent support, dispatch these
instructions as a read-only subagent, one per failing case; otherwise follow
them inline, one failure at a time. Input: the absolute paths of the case's
`runs/<run_id>/` directories produced by `evalkit runs pull --experiment`
(one per repetition), plus the repo checkout(s) of the agent under test.

## What you may touch

- **Read access to the entire repo.** Use read-only inspection (`cat`,
  `grep`, `jq`, `ls`, `git log`/`git show` — never anything that writes).
- **Hard rule: never modify the code repo.** No writes, no edits. You may
  write throwaway scratch scripts under a temp dir to reproduce or probe a
  root cause — but never create, edit, move, or delete anything inside the
  repository. Your finding IS your final output; you have no repo-write step.

## How to diagnose

1. Read every file in each run directory. Weigh every section:
   - `case.json` (`input`, `expected`, `evaluators`) and `evaluations.json` —
     what went wrong on the surface and which evaluator(s) scored low and why (read
     each `explanation`; judge-backed evaluators pack per-item verdicts into
     it as JSON).
   - each run's `trajectory.json` — its ATIF trajectory. Steps run in
     `step_id` order; each step's `tool_calls` carry structured `arguments`,
     their results are in `observation.results` (matched by `source_call_id`;
     `extra.is_error: true` marks a failed call, `extra.error_type` its kind
     when the harness sets it), and `timestamp` shows where time went.
     Delegated work is in `subagent_trajectories`, one trajectory per
     subagent. When a result was shortened by the harness, the full output is
     at its `extra.full_output_path`; fetch it with `evalkit runs files <pull
     dir> <run_id> --path <that path>` (see the run's files below).
     **This is the strongest signal for *why* it failed.** Look for: retry
     loops (same call re-issued after a non-fatal error), discovery overhead
     (probing for a schema or identifier that should be derivable first-try),
     wrong-then-right argument values, dead-end pivots. Treat these as
     diagnosable in their own right: a tool the model can't call correctly the
     first time is a real defect even when the run eventually recovers. Record
     each shape in `trajectory_patterns` and localize the surface that would
     make the correct call derivable first-try.
   - `run.json` `status`, `answer`, `metrics` and `error` — timeouts, token blowups,
     run crashes. `metrics.by_trajectory` splits tokens, cost and tool calls
     per trajectory: `main`, then one entry per subagent keyed like the
     evidence refs below, each with its `agent` name.
   - the run's files — its deliverables and other workspace files;
     read them (including images, which enter your context) to judge the
     deliverable directly. `evalkit runs pull` does not fetch them. When you
     need them, `evalkit runs files <pull dir> <run_id>` lists the tree, and
     `--path <file or folder>` fetches only those into
     `runs/<run_id>/files/<path>`.
2. Trace the surface (skill / tool / prompt / data) responsible. Open the
   actual files in the repo to confirm — cite concrete paths.
3. The run is history: it shows the agent as of `run.json` `started_at`, and
   the repo may have moved on. Run `git log --since=<started_at> -- <surface
   file>` (and `git log -S '<text from the trajectory>'` for prompt or tool
   text) and read any commit that touches the surface. Commit dates reset on
   rebase and squash merge, so also read the last few commits on the surface
   file (`git log -n 10 -- <file>`) whatever their date. If a later commit
   already fixes the failure, fill `already_addressed` with its sha and one
   line on what it changed, and keep `suggested_direction` to "re-run to
   confirm".
4. If the capability the case needs does not exist at all, that is
   `root_cause: absent` / `surface.kind: absent` — the build-new-capability
   case.
5. When the surface is a skill or another authored prompt, judge it against
   the authoring standards too — a bloated, coupled, or weakly-described skill
   is itself a candidate root cause (`skill-gap` / `prompt-ambiguity`).

## Output — return ONLY this JSON object

No prose before or after. A single JSON object matching this schema exactly:

```json
{
  "case_id": "<from case.json>",
  "verdict": "failed | borderline",
  "root_cause": "skill-gap | tool-wrong-shape | tool-wrong-data | tool-call-inefficiency | prompt-ambiguity | hallucination | data-missing | absent | unknown",
  "surface": {"kind": "skill | tool | prompt | mcp | data | absent", "name": "<surface name>", "file": "<repo path or null>"},
  "evidence": [
    {"type": "trajectory-span | evaluator | artifact | prose", "ref": "<main:<step_id> or <trajectory_id>:<step_id> / evaluator name / artifact path>", "detail": "<one specific line>"}
  ],
  "trajectory_patterns": ["<short label of a recurring shape, e.g. 'lookup -> latest_lookup retry, +1 turn'>"],
  "confidence": 0.0,
  "already_addressed": null,
  "coverage_gap": null,
  "suggested_direction": "<one or two sentences: where the fix likely belongs and why — direction, not a diff>"
}
```

Rules for the finding:

- Every `evidence` entry must cite something concrete (a step as
  `main:<step_id>` or `<trajectory_id>:<step_id>` — `subagent_trajectories[<n>]:<step_id>`
  when the subagent has no `trajectory_id` — an evaluator name, an artifact
  path, a quoted line). No vague evidence.
- Use `root_cause: tool-call-inefficiency` when the defect is *how* the agent
  reached a right-or-close answer — retried/errored/redundant calls or
  discovery overhead — rather than a wrong final call shape
  (`tool-wrong-shape`) or wrong data (`tool-wrong-data`). Point `surface` at
  the tool description or skill that would remove the wasted turns, and
  populate `trajectory_patterns`.
- `surface.file` must be a path that exists in the repo, or `null` if you
  genuinely cannot localize it (then set `root_cause: unknown` or `absent`).
- If a run's `run.json` has a non-null `error` (the agent run's own error), set
  `root_cause: unknown`, `verdict: failed`, and put the error in `evidence`.
- If a run's `run.json` has a non-null `pull_error`, evalkit could not fetch
  it in full and there is no `trajectory.json`; diagnose from the other runs
  of the case, and if none is complete, set `root_cause: unknown` and cite the
  `pull_error`.
- `confidence` is your own 0–1 estimate.
- `already_addressed` is `null`, or `{"commit": "<sha>", "detail": "<what it
  changed>"}` when a commit made after the run already changes the failing
  surface in a way that fixes it.
- `coverage_gap` is `null`, or `{"kind": "uncovered_scenario | wrong_case",
  "note": "<one line>"}`. Use `uncovered_scenario` when the failure shows a
  nearby scenario no case in the suite asserts (search the suite's case YAML
  before saying so). Use `wrong_case` when the expected answer is not
  derivable from the data or the domain, so the case itself needs fixing. A
  coverage gap never replaces the diagnosis; fill the rest of the finding as
  usual.

## EDD-infra note (optional, after the JSON)

If — and only if — you found a gap in the **eval setup itself** (data you
needed that the run directory did not contain, a missing evaluator signal, etc.), add
one short line after the JSON beginning `EDD-infra note:`. A nudge, not a
report. Otherwise omit it entirely.
