# Analyze one app run

Instructions for analyzing **one** app run (a run of the agent outside any
experiment) and returning one structured JSON observation: what the user
wanted, whether the agent served it, and how it failed. On agents with
subagent support, dispatch these instructions as a read-only subagent, one
per run; otherwise follow them inline, one run at a time. Input: the absolute
path of one `runs/<run_id>/` directory produced by `evalkit runs pull
--source app`, plus the repo checkout(s) of the agent.

There is no expected answer and no score. The verdict comes from the
trajectory alone.

## What you may touch

- **Read access to the entire repo.** Use read-only inspection (`cat`,
  `grep`, `jq`, `ls`, `git log`/`git show` — never anything that writes).
- **Hard rule: never modify the code repo.** Your observation IS your final
  output.

## How to analyze

1. Read `run.json` and `trajectory.json` end to end. Steps run in `step_id`
   order; each step's `tool_calls` carry structured `arguments`, their results
   are in `observation.results` (matched by `source_call_id`; `extra.is_error:
   true` marks a failed call), and `timestamp` shows where time went.
   Delegated work is in `subagent_trajectories`. `evalkit runs pull` does
   not fetch the run's files. If a shortened tool result is not enough, fetch
   its full output with `evalkit runs files <pull dir> <run_id> --path
   <extra.full_output_path>`; to see the deliverables, list the tree with
   `evalkit runs files <pull dir> <run_id>` and fetch only the paths you need.
   Fetched files land in `runs/<run_id>/files/<path>`.
2. Infer the user's intent: what they asked for, as of when, and what a good
   answer would have contained.
3. Look for failure signals, strongest first:
   - the user correcting the agent, re-asking the same thing, or giving up
     mid-conversation;
   - tool errors, retries of the same call, timeouts;
   - an answer that hedges, refuses without reason, or states figures no tool
     result supports;
   - a wrong tool, a missing step (data the answer needed but never fetched),
     wrong arguments;
   - wasteful steps: redundant calls, discovery overhead, dead-end pivots;
   - presentation problems: the right content badly shaped for the user.
4. Decide the verdict: `failed` (the user did not get what they needed),
   `degraded` (they got it, with real waste or friction), `ok` (no failure),
   `unclear` (the trajectory does not show enough to judge). Record `ok` runs
   too; synthesis needs them.
5. For each failure, trace the surface (skill, tool, prompt, data source) in
   the repo and name the file when you can.
6. **The run is history.** It shows the agent as of `run.json` /
   `index.json` `created_at`. Check what changed since:
   `git log --since=<created_at> -- <surface files>` and
   `git log -S '<tool or prompt text from the trajectory>'`. Commit dates reset
   on rebase and squash merge, so also read the last few commits on each
   surface file (`git log -n 10 -- <file>`) whatever their date. If a later
   commit already fixes the failure, fill `fixed_since`.
7. Write a case seed: a new, time-anchored input that exercises the same
   failure, and the ideal behaviour. Don't copy the user's words or data; the
   seed generalises the run.

## Output — return ONLY this JSON object

```json
{
  "run_id": "<from index.json>",
  "created_at": "<from index.json>",
  "intent": "<one line: what the user asked for>",
  "task_type": "<a case type from the authoring-eval-cases skill>",
  "verdict": "failed | degraded | ok | unclear",
  "signals": ["<short label, e.g. 'user re-asked', 'tool error x3'>"],
  "failures": [
    {
      "kind": "wrong_answer | wrong_tool | missing_step | inefficiency | presentation | refusal",
      "evidence": "<one specific line, citing main:<step_id> or <trajectory_id>:<step_id>>",
      "steps": [12, 14],
      "surface": {"kind": "skill | tool | prompt | mcp | data | absent", "file": "<repo path or null>"}
    }
  ],
  "ideal_behaviour": "<one or two lines: what a good run does>",
  "case_seed": {"input": "<new input, not the user's>", "anchor_date": "<YYYY-MM-DD>", "ground_truth_known": false},
  "fixed_since": null,
  "confidence": "high | medium | low"
}
```

Rules:

- `failures` is empty when `verdict` is `ok`.
- Every `evidence` line cites a concrete step, tool result or artifact.
- `ground_truth_known` is `true` only when the correct answer can be checked
  from the run's own tool results or the agent's data sources.
- `fixed_since` is `null`, or `{"commit": "<sha>", "detail": "<what it
  changed>"}` when a commit made after the run already fixes every failure
  listed.
- Never put user names, account ids or other personal data from the run into
  the observation.
