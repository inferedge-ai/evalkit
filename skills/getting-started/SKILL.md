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

## Ask one thing first

Before anything else, ask the developer which pace they want:

- **Just set it up.** Run straight through and explain at the end.
- **Walk me through it.** Say what each step does as you do it.

For the second pace, explain from the quickstart and the pages it links to
(`../evalkit-docs/concepts.md` first). Either way, stop for the developer only
at the points under "When to stop".

## Rules

- Work from the root of the developer's agent project. Cases go in
  `evalkit/cases/<dataset>/` and the run script is `evalkit/run.py`. Never put
  an `__init__.py` in the `evalkit/` folder.
- Log in with a bare `evalkit login`: it uses hosted evalkit. Pass `--url`
  only when the developer names another server.
- Find exact command shapes with `evalkit --help` and `--help` on any
  subcommand; do not guess flags.
- Hand over to the other skills where the quickstart does:
  - Step 2, a `!` line from `evalkit traces import` or `evalkit traces check`:
    the **connect-traces** skill.
  - Step 3, mining cases: the **eval-driven-development** skill, Loop 2.
  - Step 5, fixing the agent: the **eval-driven-development** skill, Loop 1.
- Mine a small first batch: the 30 most recent app runs, not the mining
  loop's default window. Import no more history than that needs.
- The cases stay in the developer's own workspace. Skip the review steps of
  the mining loop.

## When to stop

Stop and ask only for what you cannot do yourself:

- The sign-in in the browser that `evalkit login` opens.
- The trace store's URL, project name and API key. Ask for the name of the
  environment variable that holds the key; never ask for the key itself.
  Creating the trace source needs an admin: if it is refused, say so and stop.
- The case plan from the mining loop, before any case is written.

Do not ask about the mapping preset: read the agent's code, pick the preset
for its framework from `evalkit traces mapping presets`, and check it on one
session.

## Checks between steps

Do not move on until each holds:

1. After step 2: every line of the one-session import has a ✓.
2. After the range import: `evalkit runs list <agent> --source app` returns
   runs. With none, the agent has no history in the trace store: take the
   quickstart's "No trace history?" path.
3. After step 4: the baseline has at least one case that fails or runs slow.
   If every case passes, mine a wider window or add cases for the slowest
   runs before step 5.
4. After step 5: a second experiment exists for the same dataset.

## Finish

Print the two experiment URLs and the output of
`evalkit summary <new id> --baseline <old id>`. Then say in a few lines what
was set up, which cases were fixed, and how response time, cost and tokens
moved. Point at the quickstart's "Where next" links.
