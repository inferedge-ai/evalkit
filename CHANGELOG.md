# Changelog

## Unreleased

- The Harness API's run list names the tenant `org_id` instead of
  `client_id`: evalkit sends `GET /runs?org_id=…` and reads `org_id` on each
  listed run. A harness that declares `run_listing` renames both, or its
  tenant filter is ignored and its runs show no tenant. The run browser's
  filter and the MCP `find_runs` tool's filter and rows say `org_id` too.

## 0.3.0

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
  writes `config.yaml` and `.env`, migrates, registers your LLM as the LLM
  provider `try` and makes it the default judge. It prompts for anything not
  given as a flag or env var; `--yes` never prompts, `--kit-only` only copies
  the kit. See Try locally.
- `evalkit serve` and `evalkit db upgrade` read a `.env` beside
  `config.yaml`; variables already in the environment win.
- The try kit's `run.py` installs `evalkit-py` from PyPI; `evalkit init-server`
  pins it to the installed version when it copies the kit.
- The docs and an SDK API reference are published at
  https://inferedge-ai.github.io/evalkit/, one version per release plus `dev`.
- API keys and the default judge moved from the server config into the
  database, so adding an agent, evaluator or LLM provider needs no restart.
  - The server config no longer takes `credentials:` or `judge_default:`.
    If it has either, the server refuses to start and prints the commands
    that set the same values. Remove them, start the server, then run those
    commands with each key in your shell env.
  - Shared agents and remote evaluators keep their key on the server:
    `evalkit agents create|edit <ref> --api-key-env NAME` (and
    `--clear-api-key`), the same flags on `evalkit evaluators create|edit`,
    or the API key field in the UI. Keys are encrypted with
    `encryption_key` and never returned; reads show `has_api_key`.
    Personal agents and evaluators still read their key from `evalkit.yaml`.
  - Writers of a shared workspace can now set an agent's `harness_url` and
    an evaluator's endpoint, not only admins. Changing a URL removes the
    stored key unless the same call sets a new one.
  - Admins register LLM providers with `evalkit llm-providers
    create|edit|list|show|archive|restore` (or Admin → LLM providers in
    the UI) and set the default judge with `evalkit judge-default set
    --provider <name> --model <model>` (options you leave out keep their
    current value). Server runs, resumes and re-evaluations only judge with
    a registered provider; another base URL is refused with 400.
  - A server run whose agent, evaluator or provider has no key no longer
    fails with 400: it calls without a key, and the response lists
    `warnings`. Local runs print one line per agent or evaluator without
    an `evalkit.yaml` entry.
  - The server refuses shared agent, evaluator and LLM provider URLs that
    resolve to private, loopback, link-local or other non-public addresses.
    Personal agents and evaluators are not checked: your CLI calls them. Set `allow_private_endpoints: true` in the
    server config when your harness, evaluator or LLM runs in the same
    cluster or on `host.docker.internal`.
  - `evalkit.yaml`: `credentials.judges` is now
    `credentials.llm_providers`; the old name fails to load.
  - `evalkit trace-sources rekey` is now `evalkit db rekey`, and
    re-encrypts every stored key.
  - New `evalkit evaluators show` and `evalkit evaluators restore`.
  - The UI's Settings → Evaluators tab can now edit an evaluator (endpoint,
    timeout, API key) and restore an archived one, in a modal like the
    agent form.
  - Run `evalkit db upgrade` for migration `0013`.

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
