# Changelog

## Unreleased

- evalkit is on PyPI as **`evalkit-py`** (the command and the Python module
  are still `evalkit`). Install the version your server runs:
  `uv tool install --reinstall "evalkit-py==<version>"`; `curl <server>/api/cli`
  prints it. `develop` builds publish `X.Y.Z.devN` pre-releases, `main`
  builds `X.Y.Z`. CLIs older than this cannot update themselves: run that
  command once.
- The server no longer serves its own wheel: `/api/cli/wheel`,
  `/api/cli/simple` and `/api/cli/files/…` are gone, and `GET /api/cli`
  returns only `{"version", "package"}`. `EVALKIT_WHEEL_DIR` is ignored.
- `evalkit update` reinstalls the CLI from PyPI at the server's version and
  moves an `evalkit-py==…` pin in `./pyproject.toml` (then runs `uv sync`).
  `--vendor` is removed. A project with a vendored wheel moves once:
  `uv remove evalkit && uv add "evalkit-py[otel]==<version>"`.
- `pip install "evalkit-py[server]"` installs a working server, web UI
  included. New `evalkit init-server` sets one up on this machine against a
  Postgres you already run: it creates the database, copies the try kit,
  writes `config.yaml` and `.env`, and migrates. It prompts for anything not
  given as a flag or env var; `--yes` never prompts, `--kit-only` only copies
  the kit. See Try locally.
- `evalkit serve` and `evalkit db upgrade` read a `.env` beside
  `config.yaml`; variables already in the environment win.
- The try kit's `run.py` installs `evalkit-py` from PyPI; `evalkit init-server`
  pins it to the installed version when it copies the kit.
- The docs and an SDK API reference are published at
  https://inferedge-ai.github.io/evalkit/, one version per release plus `dev`.

- The server can report errors to Sentry. Add a `sentry:` block to the
  server config with `dsn` (e.g. `env:SENTRY_DSN`), and optionally
  `environment`, `traces_sample_rate` (default `0.0`) and
  `max_request_body_size` (default `never`). Without the block,
  nothing is sent.

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
