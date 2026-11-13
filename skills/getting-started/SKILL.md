---
name: getting-started
description: Use when a developer wants to set up evalkit for their agent for the first time, try evalkit, or follow the evalkit quickstart — takes them from an installed CLI to a connected agent, a dataset mined from the agent's real traces, a baseline experiment and one measured improvement. Not for a team that already runs experiments (use evalkit-usage) or for deploying a server (use evalkit-deployment).
---

# Getting started

Walk the developer through the evalkit quickstart in their agent's project
folder. At the end they have evalkit connected to their agent, a dataset
mined from the agent's own traces, a baseline experiment, and a second
experiment that shows what one round of fixes gained.

The quickstart page is the source of truth for every step and command:
[Quickstart](../evalkit-docs/quickstart.md). (`evalkit-docs/` sits beside the
installed skills; in the evalkit repository the page is
`docs/user/quickstart.md`.) Read it first and follow it in order. This skill
only adds what you need to run it for someone.

## Ask two things first

Before anything else, ask the developer:

- **Which pace.** "Just set it up": run straight through and explain at the
  end. "Walk me through it": say what each step does as you do it, from the
  quickstart and the pages it links to (`../evalkit-docs/concepts.md` first).
- **Which path** (the tabs of the quickstart's step 3). "My agent sends
  traces to Phoenix": evalkit imports its history. "No traces yet": the agent
  sends its traces straight to evalkit. Read the agent's code first and
  suggest the path it fits.

After that, stop for the developer only at the points under "When to stop".

## Rules

- When a step fails and the linked docs do not cover it, show the developer
  the exact command and error, and point them to
  https://github.com/inferedge-ai/evalkit/issues to report it.
- Work from the root of the developer's agent project. Cases go in
  `evalkit/cases/<dataset>/` and the run script is `run_evals.py` at the
  project root (in `evalkit/`, `from myagent import ...` fails). Never put
  an `__init__.py` in the `evalkit/` folder. Add `evalkit-runs/` to
  `.gitignore` before the first pull.
- Log in with a bare `evalkit login`: it uses hosted evalkit. Pass `--url`
  only when the developer names another server.
- Find exact command shapes with `evalkit --help` and `--help` on any
  subcommand; do not guess flags.
- Install `evalkit-py` into the agent's own project with the tool it already
  uses (pip in its active virtualenv, uv or poetry). evalkit needs Python
  3.12 or newer; if the project cannot use it, say so and stop. Run every
  `evalkit` command in that environment (active virtualenv, `uv run` or
  `poetry run`); there is no separate global install.
- A non-Python agent, or one in another process: follow the note at the top
  of the quickstart (a Python task that calls the agent, the agent's own
  OpenTelemetry SDK, and a session id passed in or returned).
- On the "No traces yet" path, tracing goes in the agent's own start-up code,
  never only in `run_evals.py`: app runs do not go through it. Add the
  framework's instrumentor (the Instrumentation column of
  `../evalkit-docs/framework-mappings.md`) and one session per run. Keep the
  agent token out of files the repo tracks.
- One run per case in the quickstart. For real comparisons, pass
  `repetitions` (for example `repetitions=3`).
- An async agent gets an `async def task`. Never wrap it in `asyncio.run`.
- Hand over to the other skills where the quickstart does:
  - Step 3, a `!` or ✗ on a required line of `evalkit traces import` or
    `evalkit traces check` (`session`, `root`, `mapping`, `llm`,
    `trajectory`, and `tools` when the agent called a tool): the
    **connect-traces** skill. A `!` on `unmapped` is fine.
  - Step 4, mining cases: the **eval-driven-development** skill, Loop 2.
  - Step 6, fixing the agent: the **eval-driven-development** skill, Loop 1,
    with `--apply`.
- Mine a small first batch: pick the window so it holds 30 app runs or
  fewer, and narrow it if `evalkit runs list` shows more. On the Phoenix path,
  import only that window.
- The cases stay in the developer's own workspace. Skip the review steps of
  the mining loop.
- An agent that runs as a service: restart it after each fix, before the
  next run.

## When to stop

Stop and ask only for what you cannot do yourself:

- The sign-in in the browser that `evalkit login` opens.
- Phoenix path: the trace store's URL, project name and API key. Ask for the
  name of the environment variable that holds the key; never ask for the key
  itself. Creating the trace source needs an admin: if it is refused, say so
  and stop.
- "No traces yet" path: 5 to 10 real questions to ask the agent, if the
  developer wants to pick them.
- The case plan from the mining loop, before any case is written.

Do not ask about the mapping preset: read the agent's code, pick the preset
for its framework from `evalkit traces mapping presets`, and check it on one
session.

## Checks between steps

Do not move on until each holds:

1. After step 3: `evalkit runs list <agent> --source app` returns runs, and
   one session checked with `evalkit traces import` (Phoenix) or
   `evalkit traces check` (no traces yet) has a ✓ on every required line.
2. After step 5: every run has a trajectory. A run with none says why in its
   trajectory note; follow the session id rule in
   `../evalkit-docs/sdk-runs.md` ("An agent with its own trace store" or "An
   agent in another process"), change the task and run again.
3. Before step 6: the baseline has at least one case that fails or runs slow.
   If every case passes, mine a wider window or add cases for the slowest
   runs.
4. After step 6: a second experiment of the whole dataset exists.

## Finish

Print both experiment links (each run of `run_evals.py` prints one) and
the output of `evalkit summary <new id> --baseline <old id>`. Then say in a
few lines what was set up, which cases were fixed, and how response time,
cost and tokens moved. Point at the quickstart's "Where next" links.
