# Changelog

## 0.2.0

- Review state no longer filters runs. `evalkit run`, `run_experiment` and
  the UI's Run experiment dialog execute every selected case of a shared
  dataset, whatever its review state; review decides what gets promoted.
  `--include-drafts` and `include_drafts=` are removed: drop them from
  scripts and CI, or the command fails. The launcher's Drafts checkbox and
  the experiment page's "approved only" / "includes drafts" chip are gone.
- The Draft review state is now **Unreviewed** (`unreviewed` in the API and
  `datasets list --state`). The sync report field
  `review.now_draft` is now `review.now_unreviewed`.

- `evalkit lint <dir>` (server-side) now uploads the nearest `fixtures.yaml`
  and checks fixture keys the way `--offline` does: an undeclared key is an
  error, and an unused registry entry warns when the registry sits in the
  linted directory. CI can drop the separate `evalkit lint --offline` step.
- Experiment lists show a score column only for buckets some experiment in
  the list was scored on, and add `eff` (efficiency) and `custom` (mean of
  custom-evaluator scores) when present. Unconfigured answer, presentation
  and trajectory columns no longer show empty.
- The experiment page shows the judge model and reasoning effort (hover for
  base URL, max tokens and concurrency); the case run page links to it, and
  experiment lists have an optional `judge` column (Columns menu).
- The Agents page lists archived agents in a collapsed **Archived (N)**
  section, so you can reach an archived agent's page and restore it.
- An empty workspace you cannot write to no longer suggests running
  `evalkit run` or `evalkit sync` into it; it says the workspace is read-only.
- Reviews and Runs clear an old error once a later fetch succeeds. Reviews
  shows the error below the view toggle, so you can still switch views.

- `evalkit traces check` (and `--dump`) on an unknown agent now says
  `unknown agent: <ref> — create it with evalkit agents create`, like the
  other commands.

- With `mcp.oauth` set, `/mcp`'s protected-resource metadata now lists the
  identity provider's issuer exactly as configured. It used to add a trailing
  slash (`https://id.example.com/`), which OAuth clients such as Claude could
  not match to the provider's issuer, so connecting failed.

## 0.1.0

The first public release of evalkit, a self-hosted system for improving AI
agents: mine failures from real runs into eval cases, have domain experts
review them, run them against your agent, and fix what fails. See the
[README](README.md) to self-host it or to connect to an existing server.
