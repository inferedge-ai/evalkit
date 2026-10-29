# evalkit — agent notes

## Terms

Use this vocabulary everywhere — code, specs, PRs, UI copy.

| Term | Meaning |
| -- | -- |
| Client | Any team that runs its agents through evalkit. |
| Agent harness | The client's agent system: their agent + serving infra. Not evalkit code. |
| Harness API | The HTTP contract the client's harness implements so evalkit can drive it. |
| Custom Evaluator API | The HTTP contract the client implements to expose custom (whitebox) evaluators and gauges. |
| Case | The identity of one eval question, shared by every workspace: the YAML `id`. |
| Case version | One immutable content of a case (input, expected behaviour, evaluator config), identified by `(case, content_hash)`. |
| Dataset case | Which version of a case a dataset currently holds. |
| Suite | A directory of case YAML synced into one dataset with `evalkit sync`. The directory name is the dataset name. |
| Dataset | A named set of cases in the store, synced from YAML (kind `suite`). Every dataset belongs to a workspace. |
| Sync | One write of case YAML into a dataset, recorded as a `dataset_syncs` row. |
| Workspace | A named namespace for datasets, agents and remote evaluators. Either personal or shared. |
| Personal workspace | A workspace owned by one user, created with the user and named after them (`@akshat`). |
| Shared workspace | A workspace with no owner. Admins create it, e.g. `@dev`, `@prod`. There is no special canonical workspace. |
| Member | A `writer` or `reviewer` grant on a shared workspace. |
| Shared agent | An agent in a shared workspace. The server drives it. |
| Reference | How a dataset, agent or evaluator is named: `name@workspace`. The API and UI always use the full form; the CLI also takes a bare `name`, meaning your personal workspace. |
| Local run | An experiment whose agent is in a personal workspace: the user's CLI drives the harness, runs evaluators and the judge, and writes results to the server. |
| Server run | An experiment whose agent is shared: the server's own runner drives it. |
| Developer config | `evalkit.yaml` in the client's repo: the `credentials:` map (keys for personal agents and evaluators and the judge's LLM provider), the `cases` / `fixtures` directories. Keys come from env or a `.env` beside it. |
| Experiment | One run of one dataset against one agent (× N repetitions). |
| Run | One case executed once against the agent, in a fresh session. |
| App run | A run of an agent outside any experiment: production, staging, or a developer using the agent by hand. Never "production run" when you mean "not an eval run". |
| Eval run | A run that answers one (experiment, case, repetition): an agent run linked from an experiment run. |
| Agent run | One execution of an agent: answer, status, trajectory, metrics. Owns the trajectory. |
| Experiment run | Links one (experiment, case, repetition) to the agent run that answers it. A resumed run points at the newest attempt. |
| Lease | A time-limited claim on a running experiment, renewed while its runner is alive. The server reaps experiments whose lease expires. |
| Trajectory | The full record of a run: messages, reasoning, tool calls/results, tokens, timing. Format: ATIF. Never "transcript". |
| Capability | A feature the harness declares in its capabilities response (`provides` / `accepts` / `limits`). Drives gating. |
| Evaluator | A scoring function. Compares expected vs actual, returns score + explanation. |
| Remote evaluator | A client-hosted evaluator reached through the Custom Evaluator API, stored in a workspace (`evalkit evaluators create`). |
| Evaluation | One evaluator's output on one run: a `score` or a `gauge` reading. |
| Gauge | A measurement, not a score: tokens, cost, time, tool-call counts. |
| Judge | An LLM used by an evaluator to grade an answer. Chosen per experiment; the default judge applies when the run doesn't set one. A server run's judge must use a registered LLM provider. |
| LLM provider | An OpenAI-compatible endpoint evalkit may call: a name (`openai`), a base URL and a stored key. Today only judges use providers. Admins manage them (`evalkit llm-providers`). |
| Default judge | The judge (provider, model, effort, limits) an experiment uses when it does not set its own. Set by an admin in the database (`evalkit judge-default set`). |
| Stored key | An API key saved in the database, encrypted with the server's `encryption_key`, never returned (reads show `has_api_key`). Shared agents, shared remote evaluators, LLM providers and trace sources have one. |
| Credential registry | The CLI and SDK lookup from a reference (or a judge base URL) to an API key, built from the `credentials:` map in `evalkit.yaml` (`agents`, `evaluators`, `llm_providers`). Used by local and SDK runs only; the server uses stored keys. |
| Bucket | A scorecard grouping of evaluator scores: content, trajectory, presentation, efficiency, custom. |
| Split | A selectable subset of a dataset, built from case tags. |
| Overlay | A client extension of a case in another suite: extra evaluator config attached by case id, scored only for the agents it names. |
| Review state | Unreviewed, Review requested, Changes requested or Approved. Derived from a case's current content hash and the latest decisive event at that hash; never stored as a column. Never filters a run: `evalkit run` executes every selected case. Approval in a promotion source promotes the case; personal datasets have no review state. |
| Reviewer | A member with the `reviewer` role on a shared workspace. Only reviewers approve or request changes there. |
| Changelog | The per-case list of events: synced, review requested, approved, changes requested. |
| Promotion | Approving a case in a suite of a promotion source adds that version, approved, to the same-named suite of the promotion target, with the source workspace's overlays for it. Recorded as a `dataset_syncs` row with source `promotion`. |
| Promotion source | A shared workspace an admin linked to a target (`evalkit workspaces promote-to <source> <target>`). One target per source. |
| Promotion target | The shared workspace that receives promoted cases. Fed by promotion only: `evalkit sync` and review decisions there are refused; comments stay open. One source per target, no chains. |
| Surface | The place a fix lands during EDD: a skill, tool description, prompt, or data source. |
| EDD | Eval-driven development: author case → run (red) → diagnose → fix agent → re-run (green). |
| Experiment runner | evalkit's own two-phase engine (never "harness"). |
| Phoenix | Arize Phoenix, usable as a trace source. |
| ATIF | Agent Trajectory Interchange Format — Harbor's open trajectory schema. |
| SDK run | An experiment the client's own code runs with `evalkit.sdk.run_experiment`. Always a local run: the client's process drives the task and writes runs and scores through the API. |
| Harness run | An experiment driven through the Harness API, by the server (shared agent) or the CLI (personal agent). |
| Code-driven agent | An agent without a `harness_url`: the client's own code invokes it, through the task `run_experiment` calls. Only SDK runs can run it. The agent is the client's, not evalkit's. |
| Task | The client's Python function `run_experiment` calls once per case and repetition. Takes the case input, returns the answer. |
| Collector | The server endpoint that receives OTel spans over OTLP/HTTP (`/api/otel/v1/traces`). |
| Agent token | A bearer token bound to one agent and scoped to the collector only, for deployed agents that cannot carry a person's token. |
| Raw span | A span exactly as the collector received it, stored verbatim in the `spans` table. |
| ESF | evalkit span format: the normalized span model (`EsfSpan`) every raw span is mapped into before conversion to ATIF. |
| Mapping | A per-agent JSON document that says which span attribute holds each ESF field, plus `answer` and `exclude_spans`. Every OTel agent has one, versioned draft → active → retired. The connect-traces skill ships `openinference` and `gen_ai` documents to start from. |
| Session | The set of spans that belong to one run, grouped by the session id attribute. One run is one session. |
| Settled | A session whose newest span arrived longer ago than `otel.settle_seconds`. The signal that no more spans are coming. |
| Pushed trajectory | An ATIF document a task returns directly, bypassing the collector. |
| Trajectory source | Where an agent's trajectories come from: `atif` (served by a harness or returned by a task) or `otel` (collected spans converted by evalkit). Recorded on the agent row and the experiment. |
| Trace source | A third-party trace store (Phoenix), created by an admin and used only to import history. Its API key is stored encrypted with the server's `encryption_key`. |
| Python evaluator | A custom evaluator written as a Python function and passed to `run_experiment`. The in-process twin of the Custom Evaluator API. |
| Try-it stack | The Compose kit in `skills/evalkit-deployment/try/`: server and Postgres judging with the developer's LLM (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`), plus a sample agent run through an SDK run with OTel traces. For trying evalkit on one machine; never a deployment. |
| Sample agent | The try-it stack's `agent.py`: a small agent that calls the developer's LLM with one tool and emits GenAI-convention spans. Example client code, not part of the package. |

## Layout

- `src/evalkit/contracts/` — pydantic models for the wire contracts (ATIF, Harness API, Custom Evaluator API, case YAML, ESF and the span mapping document)
- `src/evalkit/core/` — business logic: runner (`prepare_experiment` server-side; `drive_experiment` / `evaluate_experiment` anywhere; the experiment state machine and lease), evaluators, gauges, importer, lint, scorecard, pull (`core/pull.py`, the per-run layout `evalkit runs pull` writes), run digests (`core/run_digest.py`, per-run and windowed metrics from ATIF), the `Blob` protocol, judge, workspace references, access rules (`core/access.py`), review (`core/review.py`, the review state model); `core/traces/` holds the mapping engine, the ESF → ATIF converter, the settle rule, the `SessionReader` protocol and the Phoenix import adapter; `core/python_eval.py` runs Python evaluators
- `src/evalkit/store/` — `store/base.py` holds the store models and the runner's `Store` protocol; `store/blob.py` is the obstore `Blob`
- `src/evalkit/client/` — the runner's `Store`, `Blob` and `SessionReader` over the evalkit HTTP API (`HttpStore`, `HttpBlobStore`, `HttpSessionReader`), the sync `SyncApi`, the stored server login (`client/connection.py`) and the dataset sync helpers; used by the CLI and the SDK
- `src/evalkit/sdk/` — `run_experiment` / `evaluate_experiment`, task binding, the `@evaluator` decorator and `tracing.register`
- `src/evalkit/db/` — Postgres and the server's `Store` (`PgStore`): users, workspaces and members, tokens, agents, remote evaluators, trace sources, cases and case versions, datasets and syncs, overlays, fixture blobs, review events and comments, experiments, agent runs, experiment runs, evaluations, SQL scorecard aggregation, spans and mappings + alembic
- `src/evalkit/api/` — FastAPI server (`api/otel.py` is the collector, `api/traces.py` sessions and mappings, `api/tokens.py` agent tokens, `api/runs.py` and `api/pull.py` an agent's runs, `api/mcp/` the read-only MCP server at `/mcp`); `src/evalkit/cli/` — typer CLI (local runs, thin HTTP calls for everything else)
- `docs/user/` — the user pages (Try it locally, Quickstart, Concepts and the web UI's docs pages). The UI imports these files directly, the wheel bundles them, and `evalkit skills install` copies them to `evalkit-docs/` beside the skills, so skills link to `../evalkit-docs/<page>.md`; there is no second copy. Pages link to each other as `page.md`
- `ui/` — React web UI; `skills/` — distributable skills
- `deploy/` — Dockerfile (server image build; clients deploy via the evalkit-deployment skill's bundled compose); `contracts/` — generated OpenAPI/JSON Schemas

## Layering rules

- `api → core → store`; `db` may import `core`, `store/base.py` and `contracts`; `cli → client → core`; `sdk → client → core`. Arrows never reverse; `core` imports neither `sdk`, `client` nor `api`; `sdk` imports nothing from `cli`. `core/traces` has no database or HTTP-server imports; the runner reaches sessions only through the `SessionReader` on `RunnerDeps`.
- Only `store/blob.py` imports obstore. `store/` never imports `core` or `db`. `client` and `cli` may import `store/base.py` (pure models and the `Store` protocol) but never `db`, `store/blob`, or any server-only package (server-only commands live behind the `evalkit[server]` extra boundary in `cli/server_commands.py`).
- The CLI never holds database or blob credentials: local runs reach the store and blob through the evalkit API. The server's API keys are stored keys, encrypted in the database with `encryption_key`; the CLI's keys live in `evalkit.yaml` `credentials:` and env.
- The base package depends on `opentelemetry-api` only; the OTel SDK and the OTLP exporter are the `evalkit[otel]` extra, `opentelemetry-proto` is in `server`.
- evalkit code has zero knowledge of any client's agent internals: no client tool names, paths, buckets, or env vars.

## Commands

- `uv run pytest` — tests (e2e tests excluded by default)
- `uv run ruff check` / `uv run ruff format` — lint/format
- `uv run mypy src/evalkit` — types
- `cd ui && npm test` — UI tests
- `uv run python scripts/gen_schemas.py` — regenerate the OpenAPI contract docs under `contracts/` (a drift test fails CI when they're stale)
- `uv run python scripts/fetch_cost_map.py` — fetch the LiteLLM cost map (gitignored; the Docker build and the wheel build fetch it, and the server fetches it at runtime when missing)

### Local server

- `uv sync --extra server`, a Postgres database, and `skills/evalkit-deployment/config.example.yaml` copied to `config.yaml` and filled in.
- `uv run evalkit db upgrade --config config.yaml`, then `EVALKIT_ADMIN_PASSWORD=<password> uv run evalkit serve --config config.yaml` (API and UI on :8020). The password creates `admin` on the first start. It listens on 127.0.0.1; pass `--host 0.0.0.0` when an agent or harness in Docker must reach it through `host.docker.internal`.
- `cd ui && npm run dev` serves a hot-reloading UI on :5180 that proxies `/api` to :8020.
- A source checkout reports the plain semver and serves no wheel: `/api/cli` is 404 unless `EVALKIT_WHEEL_DIR` points at a directory holding one wheel of the same version.
- The try-it stack (`skills/evalkit-deployment/try/`, walkthrough in `docs/user/try-locally.md`) builds the image from this checkout: `docker compose up -d --wait --build`.
- `tests/e2e/services.py` serves a fake harness, custom evaluator and judge on ports 18080, 19000 and 19090 for e2e runs against a built image.

## CI

`.github/workflows/ci.yml` runs on PRs to `develop`/`main` (non-draft; jobs are
path-filtered against the PR base) and on pushes to those branches:

- `python` — `ruff check`, `ruff format --check`, `mypy src/evalkit`, `pytest -n auto`
- `ui` — `npm ci`, `tsc -b`, `vitest run`, `vite build` in `ui/`

## Release

- `.github/workflows/publish.yml` runs on pushes to `develop` and `main`.
  When code changed (`src/`, `ui/`, `docs/user/`, `skills/`, `contracts/`,
  packaging, Dockerfile), it builds the server image and pushes it to
  `ghcr.io/inferedge-ai/evalkit`, tagged with the full git sha and the branch
  name. It always builds the CLI wheel as a workflow artifact.
- `main` only: the image is also tagged with the version, and the workflow
  creates GitHub release `vX.Y.Z` with the wheel attached and the version's
  `CHANGELOG.md` section as notes.
- Deploying an image is the operator's job; nothing here deploys.
- Version string: `MAJOR.MINOR.PATCH+g<short sha>`. Git holds the plain
  semver in `pyproject.toml`; the workflow stamps the build id with
  `uv version` before building the image and the wheel. Feature PRs never
  bump it: `develop` builds carry the last released semver plus their sha.
- Releasing: a PR into `develop` bumps `pyproject.toml` and renames the
  `## Unreleased` changelog section to the new version; then a
  `develop → main` PR (merge commit, not squash) ships it. CI's `version`
  job blocks a PR into `main` unless the version was bumped above `main`'s
  and the changelog has that section. Entries that land under
  `## Unreleased` while the release PR is open ship in that release without
  notes; move them into the version's section if they matter. Bump MAJOR
  when the CLI and server can no longer talk, MINOR for new commands,
  endpoints or fields, PATCH otherwise.
  While evalkit is on `0.x`, a MINOR difference also counts as incompatible:
  the CLI refuses every command except `evalkit update --server <url>`.
- The image builds its wheel into `/app/dist` and serves it at
  `/api/cli/wheel` (metadata at `/api/cli`, UI page `/docs/install`). Every
  response carries `X-Evalkit-Version`; the CLI compares it to its own
  version on every call and `evalkit update` reinstalls from the server.

## Changelog

`CHANGELOG.md` has one section per released version plus `## Unreleased` at
the top. Every PR that changes what a user can do or see (a command, flag,
endpoint, field, UI control, review or run behaviour) adds its entry under
`## Unreleased`, in the same PR; the release PR renames that section.
Write it for the person upgrading: what they can do now, how to invoke it, and
what is refused or different. Internal refactors and test-only changes do
not need an entry.

## Code style

- No new docstrings or inline comments unless they state a constraint the code cannot show. Code is the single source of truth.
- f-strings everywhere except stdlib logging (`%s` placeholders); structlog uses plain kwargs.
- `X | None`, never `Optional[X]`. No local imports. No try/except unless the spec calls for it (fail fast).
- Manage deps with `uv add` / `uv remove`; run tooling via `uv run`.
