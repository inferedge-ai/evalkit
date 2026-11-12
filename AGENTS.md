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
| Local run | An experiment whose agent is in a personal workspace: the user's CLI drives the harness, runs evaluators, and writes results to the server. Judge-using built-ins run on the server unless the run brings its own judge key. |
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
| Server evaluator | A built-in evaluator the evalkit server runs for a local or SDK run, over `POST /api/experiment-runs/{id}/server-evaluations/{name}`: one call per run per evaluator; the server scores from what it stored and returns rows, the runner publishes them. |
| Local-only evaluator | A built-in that always runs on the developer's machine (`builtin_evaluator(..., local_only=True)`): `tool_trajectory_match`, `required_tools_called`, `max_response_time`. |
| Scored on | Where an evaluation was computed: `local` (the CLI or SDK process) or `server`. A column on every evaluation, separate from its `source`. |
| Gauge | A measurement, not a score: tokens, cost, time, tool-call counts. |
| Judge | An LLM used by an evaluator to grade an answer. Chosen per experiment; the default judge applies when the run doesn't set one. Names exactly one of a registered LLM `provider` (judge-using built-ins run on the server with its stored key) or a `base_url` (bring your own key: they run locally). A server run's judge must be a provider. |
| LLM provider | An OpenAI-compatible endpoint evalkit may call: a name (`openai`), a base URL and a stored key. Today only judges use providers. Admins manage them (`evalkit llm-providers`). |
| Default judge | The judge (provider name, model, effort, limits) an experiment uses when it does not set its own. Set by an admin in the database (`evalkit judge-default set`). |
| Stored key | An API key saved in the database, encrypted with the server's `encryption_key`, never returned (reads show `has_api_key`). Shared agents, shared remote evaluators, LLM providers and trace sources have one. |
| Credential registry | The CLI and SDK lookup from an agent or evaluator reference to an API key, built from the `credentials:` map in `evalkit.yaml` (`agents`, `evaluators`). Used by local and SDK runs only; the server uses stored keys. A judge the run brings carries its own key (`--judge-api-key-env`, `Judge(api_key=...)`), never stored. |
| Bucket | A scorecard grouping of evaluator scores: content, trajectory, presentation, efficiency, custom. |
| Tag | A label on a case: from its YAML `tags`, its folder in the suite, or an overlay. `evalkit run --tag` (repeatable) runs the cases carrying any of the given tags. |
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
| Mapping | A per-agent JSON document that says which span attribute holds each ESF field, plus `answer` and `exclude_spans`. Every OTel agent has one, versioned draft → active → retired. The package ships presets to start from (`core/traces/mapping_presets/`, `evalkit traces mapping presets`): `openinference`, `gen_ai` and one per popular framework. |
| Session | The set of spans that belong to one run, grouped by the session id attribute. One run is one session. |
| Settled | A session whose newest span arrived longer ago than `otel.settle_seconds`. The signal that no more spans are coming. |
| Pushed trajectory | An ATIF document a task returns directly, bypassing the collector. |
| Trajectory source | Where an agent's trajectories come from: `atif` (served by a harness or returned by a task) or `otel` (collected spans converted by evalkit). Recorded on the agent row and the experiment. |
| Trace source | A third-party trace store (Phoenix), created by an admin and used only to import history. Its API key is stored encrypted with the server's `encryption_key`. |
| Python evaluator | A custom evaluator written as a Python function and passed to `run_experiment`. The in-process twin of the Custom Evaluator API. |
| Try-it stack | The Compose kit in `skills/evalkit-deployment/try/`: server and Postgres judging with the developer's LLM (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`) on host port `EVALKIT_PORT` (default 8020), plus a sample agent run through an SDK run with OTel traces. For trying evalkit on one machine; never a deployment. |
| Sample agent | The try-it stack's `agent.py`: a small agent that calls the developer's LLM with one tool and emits GenAI-convention spans. Example client code, not part of the package. |
| SSO | Single sign-on: a person signs in through the IdP; the UI and CLI are public OAuth clients and the server verifies the IdP's JWT access token on every request. |
| IdP | The identity provider: the OAuth authorization server and OpenID provider an organisation runs. Configured once per server in `auth.oidc`. |
| Access token | The IdP-issued JWT a client presents as bearer. Its `aud` names evalkit. Verified against the IdP's JWKS; no `auth_tokens` row. |
| Subject | The pair `(issuer, sub)` identifying one identity at one IdP; keys the `users` row with the tenant. |
| Machine user | A `users` row bound to a machine client's subject: no password, no email, `kind` is `machine`. CI authenticates as one with the client credentials grant. |
| Tenant | One organisation's slice of the database; the public repo has exactly one, id 1. |
| Hosted evalkit | The evalkit server InferEdge runs for any organisation to sign up to, at `https://evalkit.inferedge.com`. `evalkit login` uses it unless `--url` names another server. A server someone else runs is self-hosted, never hosted evalkit. |

## Layout

- `src/evalkit/contracts/` — pydantic models for the wire contracts (ATIF, Harness API, Custom Evaluator API, case YAML, ESF and the span mapping document)
- `src/evalkit/core/` — business logic: runner (`prepare_experiment` server-side; `drive_experiment` / `evaluate_experiment` anywhere; the experiment state machine and lease), evaluators, gauges, importer, lint, scorecard, pull (`core/pull.py`, the per-run layout `evalkit runs pull` writes), run digests (`core/run_digest.py`, per-run and windowed metrics from ATIF), the `Blob` protocol, judge, workspace references, access rules (`core/access.py`), review (`core/review.py`, the review state model); `core/traces/` holds the mapping engine, the mapping presets (`mapping_presets/*.json`, read through `presets.py`; the only copy), the ESF → ATIF converter, the settle rule, the `SessionReader` protocol and the Phoenix import adapter; `core/python_eval.py` runs Python evaluators
- `src/evalkit/store/` — `store/base.py` holds the store models and the runner's `Store` protocol; `store/blob.py` is the obstore `Blob`
- `src/evalkit/client/` — the runner's `Store`, `Blob` and `SessionReader` over the evalkit HTTP API (`HttpStore`, `HttpBlobStore`, `HttpSessionReader`), the sync `SyncApi`, the stored server login (`client/connection.py`) and the dataset sync helpers; used by the CLI and the SDK
- `src/evalkit/sdk/` — `run_experiment` / `evaluate_experiment`, task binding, the `@evaluator` decorator and `tracing.register`
- `src/evalkit/db/` — Postgres and the server's `Store` (`PgStore`): users, workspaces and members, tokens, agents, remote evaluators, trace sources, cases and case versions, datasets and syncs, overlays, fixture blobs, review events and comments, experiments, agent runs, experiment runs, evaluations, SQL scorecard aggregation, spans and mappings + alembic
- `src/evalkit/api/` — FastAPI server (`api/otel.py` is the collector, `api/traces.py` sessions and mappings, `api/tokens.py` agent tokens, `api/runs.py` and `api/pull.py` an agent's runs, `api/mcp/` the read-only MCP server at `/mcp`); `src/evalkit/cli/` — typer CLI (local runs, thin HTTP calls for everything else)
- `docs/user/` — the user pages (Try it locally, Quickstart, Concepts and the web UI's docs pages). The UI imports these files directly, the wheel bundles them, and `evalkit skills install` copies them to `evalkit-docs/` beside the skills, so skills link to `../evalkit-docs/<page>.md`; there is no second copy. Pages link to each other as `page.md`
- `ui/` — React web UI, bundled into the wheel as `evalkit/bundled/ui` (`paths.ui_root()`); `skills/` — distributable skills
- `mkdocs.yml`, `docs/site/` — the GitHub Pages site: MkDocs Material over `docs/user/`, plus `docs/site/sdk-reference.md` (mkdocstrings), hooks, theme and overrides; `mike` versions it
- `deploy/` — Dockerfile (server image build; clients deploy via the evalkit-deployment skill's bundled compose); `contracts/` — generated OpenAPI/JSON Schemas

## Layering rules

- `api → core → store`; `db` may import `core`, `store/base.py` and `contracts`; `cli → client → core`; `sdk → client → core`. Arrows never reverse; `core` imports neither `sdk`, `client` nor `api`; `sdk` imports nothing from `cli`. `core/traces` has no database or HTTP-server imports; the runner reaches sessions only through the `SessionReader` on `RunnerDeps`.
- Only `store/blob.py` imports obstore. `store/` never imports `core` or `db`. `client` and `cli` may import `store/base.py` (pure models and the `Store` protocol) but never `db`, `store/blob`, or any server-only package (server-only commands live behind the `evalkit[server]` extra boundary in `cli/server_commands.py`).
- The CLI never holds database or blob credentials: local runs reach the store and blob through the evalkit API. The server's API keys are stored keys, encrypted in the database with `encryption_key`; the CLI's keys live in `evalkit.yaml` `credentials:` and env.
- The base package depends on `opentelemetry-api`, the OTel SDK and the OTLP HTTP exporter; `opentelemetry-proto` is in `server`.
- evalkit code has zero knowledge of any client's agent internals: no client tool names, paths, buckets, or env vars.

## Commands

- `uv run pytest` — tests (e2e tests excluded by default)
- `uv run ruff check` / `uv run ruff format` — lint/format
- `uv run mypy src/evalkit` — types
- `cd ui && npm test` — UI tests
- `uv run python scripts/gen_schemas.py` — regenerate the OpenAPI contract docs under `contracts/` (a drift test fails CI when they're stale)
- `uv run --group docs mkdocs serve` — the docs site on :8000 (`mkdocs build --strict` is the CI check)
- `uv run python scripts/fetch_cost_map.py` — fetch the LiteLLM cost map (gitignored; the Docker build and the wheel build fetch it, and the server fetches it at runtime when missing)

### Local server

- `uv sync --extra server`, a Postgres database, and `skills/evalkit-deployment/config.example.yaml` copied to `config.yaml` and filled in.
- `uv run evalkit db upgrade --config config.yaml`, then `EVALKIT_ADMIN_PASSWORD=<password> uv run evalkit serve --config config.yaml` (API and UI on :8020). The password creates `admin` on the first start. It listens on 127.0.0.1; pass `--host 0.0.0.0` when an agent or harness in Docker must reach it through `host.docker.internal`.
- `cd ui && npm run dev` serves a hot-reloading UI on :5180 that proxies `/api` to :8020.
- Without a checkout: `pip install "evalkit-py[server]"`, then `evalkit init-server` (creates the database, writes `config.yaml` + `.env`, migrates) and `evalkit serve` in the folder it made.
- `serve` and `db upgrade` load the `.env` beside `config.yaml`; the environment wins.
- A source checkout reports the plain semver; `GET /api/cli` returns `{"version", "package"}`.
- The try-it stack (`skills/evalkit-deployment/try/`, walkthrough in `docs/user/try-locally.md`) builds the image from this checkout: `docker compose up -d --wait --build`.
- `uv run python -m tests.e2e.services` serves a fake harness, custom evaluator and judge on ports 18080, 19000 and 19090, and a fake identity provider on port 19100 (issuer `http://127.0.0.1:19100`, client id `evalkit-ui`, machine client `ci` / `e2e-secret`; see `tests/e2e/README.md`), for e2e runs against a built image.

### UI changes in a PR

- Run the server and UI locally (see "Local server"; a throwaway Postgres,
  dummy keys and the fake services in `python -m tests.e2e.services` are enough).
- Click through every page and flow the PR changes and take screenshots.
- Add them to the PR description under "Screenshots" with
  `gh pr edit <n> --body-file body.md --attach ./shot.png ...`: write
  `![caption](./shot.png)` in `body.md` and gh uploads each file to GitHub and
  rewrites the link. Never commit screenshots to the repo.
- `tsc`, `vitest` and `vite build` passing is not a UI test.
- No browser `alert()`, `confirm()` or `prompt()` in new UI code: use inline
  fields or dialogs.

## Extending evalkit

Another package can serve evalkit with API routes and UI pages of its own.

**API routes.** Build the app with `create_app(cfg, ui_dist=..., resolve_tenant=...)`,
then add plain FastAPI routes with `app.include_router(...)`.

- The UI is served by FastAPI's `app.frontend()`, which is tried only after
  every other route, so routes added after `create_app` answer GET.
- evalkit's routes come first and win a path clash, including ones with
  parameters such as `/api/agents/{ref}`. Keep yours under a prefix of your own
  (`/api/billing/...`).
- A route that needs a signed-in user is added with
  `dependencies=SIGNED_IN` from `evalkit.api.auth`; without it the route is
  public. `SIGNED_IN` covers path operations (`@router.get` and the like), not
  `router.add_route()` or mounts. A handler that needs the user takes
  `CurrentUser`; an admin-only one takes `AdminUser`.
- To answer the config per request, for example per tenant, replace `get_config`
  (`evalkit.api.deps`) in `app.dependency_overrides`. To decide only the
  private-address rule (`allow_private_endpoints`) per request, replace
  `get_allow_private_endpoints` from the same module instead: every
  private-address check reads it, and each runs on a signed-in route, so the
  replacement may take `CurrentUser`. A URL whose origin is in the config's
  `trusted_endpoints` passes every check whatever it returns. The list is read
  through `get_config`, so set it on the `Config` you pass to `create_app`, and
  on every config a replaced `get_config` returns. `create_app` refuses a bad
  entry only in the config passed to it, so build any other config with
  `Config.model_validate`, not `model_copy`.

**UI pages.** The UI takes extra pages and nav items from
`ui/src/extensions/index.tsx`, which exports `extensions` of type
`UiExtensions` (`ui/src/lib/extensions.ts`). In evalkit it is empty.

- Clone evalkit at the tag `vX.Y.Z` of the `evalkit-py` the server runs, replace
  the `ui/src/extensions/` folder with yours, build the UI there
  (`npm ci && npx tsc -b && npx vite build`) and pass `ui/dist` as `ui_dist`.
  `tsc -b` checks your pages against evalkit's types. A Vite alias to a module
  outside `ui/` does not work: imports in that module, such as `react`, do not
  resolve.
- `routes` render for signed-in users inside the app shell, beside the left
  sidebar. Start each page with `PageHeader` from
  `@/components/widgets/PageHeader` (title, description, breadcrumbs,
  actions), and show an empty state with `Empty` from
  `@/components/widgets/Empty`.
- `publicRoutes` render for everyone, as evalkit's `/docs` pages do: under
  the public navbar (Evalkit, Docs, the theme switch and Sign in) for a
  signed-out visitor, and inside the app shell for a signed-in person. They
  suit pages such as terms of use or pricing; their data must come from
  public endpoints. A public route with the same path as one of evalkit's,
  `/` included, loses to evalkit's page.
- `landingPage` is what a signed-out visitor sees at `/`, under the public
  navbar, whose Evalkit link then goes to `/`. Signing out lands there. A
  signed-in person at `/` still gets the workspace
  list, and a signed-out visitor opening any other page, such as
  `/experiments/12`, still goes straight to sign-in and back. With
  `landingPage: null`, `/` sends a signed-out visitor to sign in and
  signing out lands on `/login`.
- Link to sign-in with `SignInLink` from `@/auth/SignInLink` (`next`,
  default `/`; `prompt`; `className`). It opens `/login`, which starts
  sign-in at once even right after a sign-out in that browser; a plain link
  to `/login` then stops at "You're signed out". `/login?prompt=<value>`
  passes `prompt` to the identity provider's authorize request, so
  `<SignInLink prompt="create">` opens the provider's sign-up form where it
  supports OpenID Connect Prompt Create.
- Pages may use the shadcn components in `@/components/ui/` (`Button`,
  `buttonVariants`, `Card` and the rest), which keep shadcn's names and
  props.
- A `NavItem` may set `icon`, a `lucide-react` icon; the collapsed sidebar
  shows a puzzle piece for one without. `nav` items get a sidebar section of
  their own after Observe.
- Settings pages go in `accountSettings` (everyone) and
  `organizationSettings` (admins only). Each is a `SettingsPage`: `name`,
  `label`, optional `icon` and `element`. The page renders at
  `/settings/<name>` inside the Settings area, with its sidebar, after
  evalkit's own items in that section. A name is lowercase letters, digits
  and dashes. evalkit drops a page whose name isn't, is one of its own
  (`workspace`, `users`, `workspaces`, `trace-sources` and
  `llm-providers`), or is already taken by an earlier page in either list,
  `accountSettings` first. `organizationSettings` only hides the item from
  non-admins; the page and its endpoints must check admin themselves.
- Every `nav` item and every Settings page the person can see is a page in
  ⌘K search.
- Keep the folder path, the `extensions` export, `UiExtensions`,
  `PageHeader`, `Empty`, `SignInLink`, `/login`'s `next` and `prompt`,
  `SIGNED_IN`, `get_config` and
  `get_allow_private_endpoints` stable: other packages build against them.

**AG Grid Enterprise.** evalkit's tables are AG Grid Community grids
(`ui/src/lib/DataGrid.tsx`), and evalkit never imports `ag-grid-enterprise`,
whose licence is commercial. A package that extends evalkit can turn
Enterprise on from its extensions folder: in the clone, install
`ag-grid-enterprise` at exactly the `ag-grid-community` version evalkit
installs (`npm ls ag-grid-community`), then in `ui/src/extensions/index.tsx`
register `AllEnterpriseModule` with `ModuleRegistry.registerModules` and set
the licence key with `LicenseManager.setLicenseKey`. Features that need
per-table grid options, such as the side bar or pivoting, aren't exposed yet.

## CI

- **Open pull requests as drafts** (`gh pr create --draft`). CI skips drafts, so run the tests locally and mark the PR ready (`gh pr ready <number>`) when it should be reviewed and run CI.

`.github/workflows/ci.yml` runs on PRs to `develop`/`main` (non-draft; jobs are
path-filtered against the PR base) and on pushes to those branches:

- `python` — `ruff check`, `ruff format --check`, `mypy src/evalkit`, `pytest -n auto`
- `ui` — `npm ci`, `tsc -b`, `vitest run`, `vite build` in `ui/`

## Release

- `.github/workflows/publish.yml` runs on pushes to `develop` and `main`.
  When code changed (`src/`, `ui/`, `docs/user/`, `skills/`, `contracts/`,
  packaging, Dockerfile), it builds the server image and pushes it to
  `ghcr.io/inferedge-ai/evalkit`, tagged with the full git sha and the branch
  name. It always builds the wheel (web UI included) as a workflow artifact,
  and on `main`, when code changed, it publishes it to PyPI as `evalkit-py` (job `pypi`,
  trusted publishing through the `pypi` environment).
- `main` only: the image is also tagged with the version, and the workflow
  creates GitHub release `vX.Y.Z` with the wheel attached and the version's
  `CHANGELOG.md` section as notes.
- Deploying an image is the operator's job; nothing here deploys.
- Version string: `main` builds are `MAJOR.MINOR.PATCH`; every other build is
  the PEP 440 pre-release `MAJOR.MINOR.PATCH.dev<run number>`. Git holds the
  plain semver in `pyproject.toml`; the workflow stamps `.devN` with
  `uv version` before building the image and the wheel, so both share one
  version. Feature PRs never bump it: `develop` builds carry the last
  released semver plus `.devN`.
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
- PyPI is the only package index. `GET /api/cli` returns the server's
  version; every response carries `X-Evalkit-Version`. The CLI compares it to
  its own version on every call, and `evalkit update` reinstalls
  `evalkit-py==<server version>` from PyPI.
- The docs site: `ci.yml`'s `python` job runs `mkdocs build --strict` on PRs;
  `publish.yml`'s `docs` job deploys it with `mike` to the `gh-pages` branch,
  `dev` from `develop` and `X.Y.Z` (alias `latest`) from `main`.

## Changelog

`CHANGELOG.md` has one section per released version plus `## Unreleased` at
the top. It is a general record of what changed in each version, for anyone
reading it: users, operators, contributors. Every PR that changes behaviour,
an interface, a dependency, the schema, packaging or the build adds its
entry under `## Unreleased`, in the same PR; the release PR renames that
section. An entry says what changed and, when it is not obvious, why; name
the command, flag, endpoint, field or config key so a reader can find it.
Note anything refused or different from before in the same entry. Test-only
changes and refactors with no visible effect do not need an entry.

## Specs and plans

Design specs, implementation plans and other planning documents are not
checked in to this repo. Share them in the pull request instead: paste them
into the description or link to them.

## Code style

- No new docstrings or inline comments unless they state a constraint the code cannot show. Code is the single source of truth. The one exception: the public `evalkit.sdk` API (`evalkit.sdk.__all__`) has Google-style docstrings, because they are its published reference.
- f-strings everywhere except stdlib logging (`%s` placeholders); structlog uses plain kwargs.
- `X | None`, never `Optional[X]`. No local imports. No try/except unless the spec calls for it (fail fast).
- Manage deps with `uv add` / `uv remove`; run tooling via `uv run`.
