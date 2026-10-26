# Synthesize a fix proposal

Instructions for synthesizing one consolidated fix proposal from the per-case
findings produced by the diagnose step. Run once per experiment — as a
subagent where supported, else inline. Inputs: the `findings/` directory (one
`<case_id>.json` per failure), the `index.json` path, and the path to write
the proposal (`<review-dir>/proposals.md`).

## What you may touch

- **Read access to the entire repo** (read-only inspection).
- **Writing is allowed for exactly one file: the proposal path you were
  given.** Do not write or edit anything else.

## Your job

1. Read every finding in `findings/` and `index.json`.
2. Group findings two ways:
   - by `(surface, root_cause)` — the direct fixes;
   - by cross-cutting `trajectory_patterns` that recur across cases even when
     surfaces differ (these are the highest-leverage skill/prompt improvements
     — treat them as first-class). Tool-call inefficiency belongs here: the
     fix is usually a clearer, self-contained tool description — better arg
     docs, an example call, sharper error messages — so the correct, minimal
     call is derivable the first time. Make the tool stand on its own rather
     than leaning on a companion skill, and never a per-case patch.
3. For each group, open the cited files and pin the exact change: path +
   section + concrete content or diff. Verify the path exists.
4. Fix at the right surface. Propose **creating** a new skill / tool / handler
   only for `surface.kind == absent` / `root_cause: absent` where no existing
   surface can host the capability, or where the developer explicitly asked
   for a new one; otherwise change the existing tool interface, skill, or
   prompt. When you do propose a new surface, name it, place it, sketch its
   contract, and call it out as a new-surface proposal so the human can
   confirm.
5. Write the proposal to the given path.

## Interpreting the findings

Findings are not uniform inputs — read their flags before grouping:

- **`confidence`** weights a finding inside its group: a group built only on
  low-confidence findings is itself weak — say so in the group's diagnosis.
- **`verdict: borderline`** findings support a group but don't justify one on
  their own; list a borderline-only pattern under the summary, not as a fix
  group.
- **`already_addressed`** findings name a commit made after the run that
  already fixes the failure. Keep them out of fix groups; list them under
  "Already fixed since the run — re-run to confirm" with the commit and case
  id. Before writing any group, also check `git log` since the oldest run in
  the group for changes to its surface, so the proposal never repeats a fix
  that has already landed.
- **`coverage_gap`** findings say a scenario has no case, or the case itself
  is wrong. Collect them into the "Case proposals" section, one entry each,
  apart from the fix groups; they are never applied as agent changes. A
  finding can carry both a fix and a coverage gap; then it appears in both.
- **`root_cause: unknown`** findings (including run-crash cases) don't belong
  in any fix group. Collect them into an escalation list at the end of the
  proposal with their evidence.
- **`EDD-infra note:` lines** attached to findings are about the eval setup,
  not the agent. Carry them through verbatim into your own EDD-infra section
  at the end — never convert one into a proposed agent fix.

## Constraints on proposed prompt text — read carefully

The repo is full context for *your reasoning*, but the **content you propose
to write into any application prompt surface must stay general-purpose**. This
governs proposed text for skills, tool descriptions, system-prompt parts, and
any agent prompt strings:

- **No eval / golden-dataset awareness.** Never write "the eval expects…",
  "the reference answer uses…", "to match the scorer…", or mention evals,
  regression tests, or expected outputs. These surfaces must not know they are
  evaluated. Justify every recipe on intrinsic data/workflow correctness.
- **No calling-application concepts.** These surfaces do not know who invokes
  them. Refer to intent abstractly: "the query", "the request".
- **Keep surfaces decoupled.** A skill, a tool description, and a prompt part
  must each stand on its own. Don't hard-code one surface into another —
  cross-references couple the surfaces, so changing one silently breaks the
  other. Describe a capability by its intent, not by naming the other surface
  that provides it.
- **Minimal, generic, declarative.** Prefer extending an existing section over
  adding a new top-level one. No examples that smuggle in eval-only nuance.

**Fix from first principles, not case by case.** A failure shows you the
case's expected answer. Justify every proposed instruction by how the
data/domain actually works — never by the fact that you've seen that answer.
Test each one: *would it still be correct to someone who had never seen this
eval's expected answer?*

- **Yes** → it's a real, general improvement; propose it. (e.g. "the current
  rating is in `latest_rating`; `rating` is historical" — true about the data,
  regardless of any eval.)
- **No, but a general rule does explain it** → you've written a case-specific
  patch. Don't propose that — generalize it to the underlying principle that
  produces the right answer for this case *and* every sibling.
- **No, and no general rule explains it** → the expected answer isn't
  derivable from the data/domain at all, which means the eval case itself is
  likely the bug. Don't bake the specific answer into any prompt; surface it
  to the developer as a possibly-wrong case (a dataset fix), and mark the
  proposal `unverified`.

## Prompt-change guidelines (apply to every proposed prompt edit)

1. **Describe, don't legislate.** The first fix states facts about the
   mechanism — what a tool does, what state the world is in afterwards — not
   imperative rules. Strong wording (NEVER / ALWAYS / MUST) is an escalation,
   not a default register.
2. **Escalate only on observed persistence.** Reach for rule-style wording
   only after a re-run shows the failure survived the descriptive fix; cite
   the experiment that justified the escalation.
3. **Prefer deterministic guards over prompt text.** If a tool can reject the
   bad call, add the guard — prompt wording then only has to explain, not
   enforce.
4. **Error messages are prompts.** When rejecting a call, echo the inputs the
   caller actually sent, name the offending value, and state the shape a
   correct call has.

## Output — `proposals.md`

```markdown
# Fix proposals — <experiment>

## Summary
- N failures total
- By root cause: skill-gap (X), tool-wrong-shape (Y), absent (Z), ...
- Recurring trajectory patterns: <one line each>

## Cross-cutting trajectory fixes (highest leverage)

### <pattern label>
**Cases:** `id-1`, `id-2`, ...
**Pattern:** <1–2 lines: the recurring shape + its cost in turns/latency>
**Proposed change** — **File:** `<path>` — under `<section>`:
> <concrete content / diff>

## <root-cause> · <surface>

**Cases:** `id-a`, `id-b`
**Diagnosis:** <1 paragraph>
**Proposed change** — **File:** `<path>` — under `<section>`:
> <concrete content / diff>
**Verified against repo:** yes | unverified (<why>)

(repeat per group)

## Case proposals

- `<case_id>` · uncovered_scenario | wrong_case — <the note>; suggested case:
  <one line, or "fix the case: <what is wrong>">

## Already fixed since the run — re-run to confirm

- `<case_id>` · `<commit>` — <what it changed>
```

Rules: be code-shaped, not advisory — "add this rule under section X" beats
"improve the skill". Every change cites a real path. Mark anything you could
not check against actual code as `unverified` with the reason.

## EDD-infra note (optional, at the end)

If you hit a gap in the **eval setup itself** while synthesizing, add one
short `EDD-infra note:` line at the very end. A nudge, not a report. Otherwise
omit.
