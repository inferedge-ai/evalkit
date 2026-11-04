# Changelog

## Unreleased

- evalkit now uses `httpx2` instead of `httpx` and `httpx-retries`. The
  CLI, the SDK and the server make their HTTP calls through `httpx2`, with a
  small retry transport of evalkit's own (`evalkit.client.retry`). Code that
  embeds `evalkit.sdk` now installs `httpx2`, not `httpx`. A request whose
  body is a one-shot stream is now sent once and never retried (the old
  library retried it, and the second send of a spent stream failed). The
  try-it stack's sample agent (`try/agent.py`) and the SDK-runs doc sample
  import `httpx2`.
- The database gains a `tenants` table with one row, `default`, and a
  `tenant_id` on `users`, `workspaces`, `cases`, `llm_providers`,
  `trace_sources` and `fixture_blobs`. Usernames, workspace names, case ids,
  provider names and trace source names are now unique per tenant. The
  default judge moved from `server_settings` onto the tenant row. Migration
  `0016`; no behaviour change for a single installation.
- Blob objects now live under `tenants/<id>/`. After `evalkit db upgrade`,
  run `evalkit blobs relocate --config <config>` once to move existing
  trajectories, artifacts, run workspaces and fixtures; the server refuses to
  start while any object remains under the old layout. The upgrade has
  downtime between the migration and the relocation.
- `GET /api/workspaces/...` payloads include `tenant_id`.
- Single sign-on through any OIDC identity provider that issues JWT access
  tokens: set `auth.oidc` (`issuer`, `client_id`, `audience`, `name`) in the
  server config. `evalkit login` then opens the browser at the identity
  provider and stores its tokens, which refresh themselves;
  `--no-browser` uses the device authorization grant. The CLI makes its
  OAuth requests with Authlib (its httpx2 `OAuth2Client`). The first sign-in
  creates the user; `GET /api/auth/providers` reports what the server
  offers and `POST /api/auth/oidc/register` creates the user from the ID
  token. See `docs/user/sso.md`.
- `auth.password_login` (`admin` by default when `auth.oidc` is set, or
  `all`) governs `POST /api/auth/login`; non-admins get 403 under `admin`.
  Stored password tokens keep working until they expire.
- CI authenticates as a machine client with `EVALKIT_OIDC_CLIENT_ID` and
  `EVALKIT_OIDC_CLIENT_SECRET` (client credentials grant). An admin binds
  the client's subject with `evalkit users add <name> --subject <sub>`;
  `evalkit users add` also takes `--password` for a password user.
  `evalkit users bind <name> --subject <sub>` links an existing user
  (`POST /api/users/{username}/identity`). `users list` shows `kind` and
  `email`; `users promote` refuses a machine user.
- `/mcp` verifies tokens with the same settings as `/api`; a token for the
  configured audience or for `<base_url>/mcp` is its evalkit user, and
  archiving the user cuts off MCP. `mcp.oauth` is removed: a config that
  still has it fails to load and names `auth.oidc`. `create_app` no longer
  takes `mcp_jwt_verifier`.
- `tracing.register`, `tracing.exporter` and `tracing.processor` require
  `EVALKIT_OTEL_TOKEN` (an agent token); the fallback to a login token is
  removed.
- `SyncApi` and `AsyncApi` take an `httpx2.Auth` instead of a token string;
  `evalkit.client.oidc.static_auth(token)` wraps a token.
- Dependencies: `authlib` (with `joserfc` and `cryptography`) is now a base
  dependency; `pyjwt[crypto]` is in the `server` extra.
- The docs site root (`https://inferedge-ai.github.io/evalkit/`) now serves
  the latest release's pages directly, version switcher included, instead of
  a JavaScript redirect to `latest/`, so `curl`, `wget` and agents' fetch
  tools read real pages there. The "older or unreleased version" banner text
  appears only on older or `dev` pages and links to the same page under
  `latest/`. Every release still keeps its own version folder.
- New `evalkit traces mapping activate <agent> <version>` makes that
  version the active mapping and prints the version it replaced. Pass the
  version `mapping push` printed, so the draft you checked is the one that
  goes live even if someone pushes another draft meanwhile; an older
  version rolls back. `mapping push --activate` is removed: push uploads
  the draft, activate makes it live. The `activate` query parameter on
  `PUT /api/agents/{agent}/mappings/draft` is removed too, and
  `POST /api/agents/{agent}/mappings/{version}/activate` now returns
  `replaced_version`.
- `evalkit traces mapping push` with a document equal to the current draft
  keeps the draft's version and says it is unchanged, instead of minting a
  new version. The endpoint's response has a new `unchanged` field.
- `evalkit datasets list --workspace dev` lists only the datasets in `@dev`,
  and `evalkit review queue --workspace dev` only the cases waiting there.
  Without the flag both still cover every workspace you can see. `@dev` works
  too; an unknown workspace is an error, not an empty list (the same 404
  applies to `GET /api/datasets?workspace=` and `GET /api/review/queue?workspace=`). Passing
  `--workspace` together with a dataset is refused: name it as `finance@dev`.
- `evalkit datasets show finance@dev` prints one dataset's kind, last sync
  (time, author, sha, branch), case count, cases per review state (reviewable
  suites only) and tag counts.
- `evalkit workspaces list` now shows promotion links: `promotes to prod` on
  the source row and `promoted from dev` on the target row.
- Review CLI fixes:
  - `evalkit review show <dataset> <case>` prints the case (id, input,
    expected, evaluators with overlays applied, file names, tags) as YAML
    above the changelog. `--yaml` prints only the case.
  - `evalkit review queue --changes-requested` names the author the case went
    back to: "by <reviewer> to <author>". The author is the last person who
    asked for review; a re-sync does not change it. The queue API returns it
    as `author`.
  - `evalkit sync` says that edited cases under review are back in review and
    need no second `review request`.
  - `evalkit review request` skips cases already waiting on the same reviewer
    and lists them, instead of adding a duplicate changelog event. The API
    returns them as `already_requested`.
  - `evalkit review comment` takes `--comment "text"` like `approve` and
    `request-changes`. The positional form still works.
- `evalkit runs pull` now writes `started_at` in every `run.json`. A run with
  no agent-run record (most app runs) takes it from the run's listed
  `created_at`; before, the field was missing. It is `null` when the harness
  listing gives no creation time.
- The eval-driven-development skill gives both run forms: `evalkit run` for
  a Harness API agent, and the client's `run_experiment(..., case_ids=[...])`
  script for a code-driven agent. Loop 2 groups runs by `question_kind` (free
  text) instead of a `task_type` no skill defined.
- Traces from Google ADK / Gemini and LangChain-style instrumentors now keep
  the model's messages and tool calls. evalkit reads the message roles
  `model`, `ai` and `bot` as `assistant`, `human` as `user`, `developer` as
  `system` and `function` as `tool`, in any letter case. Before, it dropped
  those messages from the trajectory without a warning, so tool evaluators
  scored 0.
  - `evalkit traces check` now warns on the `llm` line when any output
    message has a role other than `assistant`, `user`, `system` or `tool`,
    and lists those roles: the trajectory drops such messages.
- Trajectories built from OTel spans are right for more agent shapes, and
  `evalkit traces check` and the SDK dry run say when one looks wrong.
  - An AGENT span becomes a subagent trajectory only when it sits under a
    tool span or carries the `tool_call_id` that started it. Other AGENT
    spans are merged into the main agent: outer wrapper spans (OpenAI
    Agents, LangChain, CrewAI), graph nodes tagged AGENT (LangGraph) and one
    root span per chat turn. **Changed:** sibling agents that hand over
    without a tool call (e.g. CrewAI sequential tasks) are now one
    trajectory instead of one subagent each; list them in the mapping's
    `subagent_spans` to keep them apart.
  - Each new user message shows up as a `user` step, so a chat's later turns
    are in the trajectory, not only the first message. With one root span
    per turn, the answer comes from the last turn.
  - Mapping fields: `exclude_spans` entries may be a name glob
    (`"OpenAILike.*"`), an attribute match (`{"attribute": K, "equals": V}`)
    or an instrumentation scope (`{"scope": "mcp"}`). New `rekind_spans`
    (`[{"name": "agent", "kind": "chain"}]`), `main_agent` and
    `subagent_spans` use the same matches.
  - `evalkit traces check` warns (`!`) when tool spans exist but the
    trajectory has no tool calls, when there are more LLM spans than LLM
    steps, and when subagents hold every LLM call and the main agent none.
    A missing root span now says how to fix it. Excluded spans no longer
    count as "without messages".
  - `run_experiment(dry_run=...)` prints the same check lines.
  - The collector stores each span's instrumentation scope. **Migration
    0015** adds the `spans.scope` column; run `evalkit db upgrade` before
    starting the new server.

- Scoring experiments of more than about 40 runs no longer fails with `500`s
  on `POST /api/experiment-runs/{id}/evaluations`.
  - A run (or `evalkit evaluate`) now scores at most 16 runs at once instead
    of all of them. Change it with `--evaluation-concurrency N` on `evalkit
    run` and `evalkit evaluate`, `evaluation_concurrency=N` in the SDK's
    `run_experiment` and `evaluate_experiment`, or the **Evaluation
    concurrency** field in the web UI's run dialog.
  - A server run uses `runner.evaluation_concurrency` from the server's
    `config.yaml` (default 16). The CLI flag, the run dialog and the
    `evaluation_concurrency` field on `POST /api/experiments`, `/resume` and
    `/evaluate` may ask for that much or less; more is refused with `400`.
    The experiment keeps the value it was started with, so a resume or
    re-evaluate without one (such as the UI's **Re-evaluate** and **Resume**
    buttons) uses it again.
  - Each scoring request uses one database connection instead of two, and
    the server's connection pool is bigger by default. Set it in
    `config.yaml` with `db.pool_size` (default 20), `db.max_overflow`
    (default 10) and `db.pool_timeout_seconds` (default 5). Keep
    `(pool_size + max_overflow) × replicas` well under Postgres
    `max_connections`.
  - When no connection frees up within `pool_timeout_seconds`, the server
    answers `503` with `Retry-After`, and the CLI and SDK retry instead of
    failing the experiment. Server runs write straight to the database, so
    they get no retry: a server run that cannot get a connection still
    fails, and `evalkit evaluate` finishes it.
- The judge no longer has a `concurrency` setting: `evaluation_concurrency`
  replaces it. `evalkit judge-default set --concurrency` is gone, and the SDK's
  `Judge(concurrency=...)`, the API and saved experiments and default judges
  ignore the field.
- `evalkit summary <N> --baseline <M>` now shows what changed since the
  baseline: a `vs experiment M` header, a Δ next to every bucket and
  evaluator, and a `changed cases:` block listing improved, regressed, new
  and removed cases (base → current score and Δ), unscored cases (no
  overall score on one side), plus a count of unchanged ones. When the two
  runs scored an evaluator on different case counts, its line shows the
  base's count too (`(3 cases, base 2)`). It reads the same compare data as the web UI's compare page.
- The summary's failing-cases list now also includes cases where any single
  evaluator scored below 0.80, naming those evaluators, even when the case's
  overall score is above 0.80.
- The case YAML schema is published. A new **Case schema** docs page (also in
  the web UI's docs) lists every key with one example per evaluator, and maps
  each scorecard bucket to its YAML key. `evalkit contracts export` now also
  writes `cases-v1.json`.
- `evalkit lint` schema errors are easier to act on. They print the file path
  and the case id, list the valid keys where an unknown key was found, and say
  where a misplaced evaluator belongs (e.g. "`presentation_judge` goes under
  `evaluators.output`"). `evalkit lint --offline` now reports these as findings
  instead of stopping with a traceback.
  Lint reports every broken case in a file, not just the first.
- Stricter case files: in a file with `cases:` or `overlays:`, any other
  top-level key besides `defaults` is now an error (it used to be ignored, so
  a typo like `defualts:` dropped your defaults silently). `cases:` and
  `overlays:` must be lists. Fix: remove or rename the stray key.
- `ScoreItem`, `EvalCase` and `EvalRun` can be imported from `evalkit.sdk`.
- `evalkit init-server --port 9500` writes `server.base_url` with that port and
  prints the matching `evalkit login` command. `evalkit serve` now defaults
  `--port` to the port in `server.base_url` (8020 when it has none). In
  the Docker try stack, set `EVALKIT_PORT` in `.env` to publish the server on
  another host port. Try it locally says how to change the port once and its
  examples use the server's own URL in the web UI.
- evalkit now runs on Python 3.12 as well as 3.13 (`requires-python` was
  `>=3.13`). Projects on 3.12 can `uv add "evalkit-py[otel]"` without
  upgrading Python. The install docs and skills state the minimum version.
- Run several tags in one experiment. `evalkit run finance --tag ratios --tag
  statements --agent ouragent@dev` runs every case carrying any of the given
  tags. In the web UI's run dialog, the **Tags** choice is now a multi-select
  (type to search, pick several). The picked tags show as removable chips
  under it, and the CLI preview prints `--tag` flags.
  `evalkit review request --tag` is repeatable too.
- Datasets in a personal workspace now have a **Run experiment** button too.
  The dialog lists your own agents first. In place of starting the run, it has
  a **Copy CLI command** button: set the options in the UI, then paste the
  `evalkit run …` command into your terminal.
- The experiment page's case table has a tag filter: pick one or more tags to
  show only the cases carrying any of them.
- "Split" is now called "tag" everywhere:
  - The SDK's `run_experiment(split="x")` is now `run_experiment(tags=["x"])`.
  - The API's `selection` takes `tags: [...]` instead of `tag`. A request that
    still sends `tag` is refused.
  - `GET /api/datasets/{ref}/splits` moved to `GET /api/datasets/{ref}/tags`.
  - The dataset page's Splits tab is now Tags, and ⌘K searches Tags.
- `evalkit run finance/<name>` no longer falls back to selecting a tag when
  `<name>` is not a folder or file of the dataset on disk. It fails and tells
  you to pass `--tag <name>`. Folders and case files still work.
- Migration `0014` rewrites the stored selection of existing experiments from
  `tag` to `tags`. Run `evalkit db upgrade` before starting the new server.

- New docs page **CI sync** (`/docs/ci-sync`): how to lint eval cases on
  PRs and sync them into a shared workspace on merge, with a GitHub Actions
  example. The Workspaces page has a new section, **Example setup:
  personal → dev → prod**, with the commands to set it up and the day-to-day
  flow of a case.

- `evalkit runs pull` no longer fails the whole batch with a 500 when a
  run's stored trajectory is missing from the blob store. That run comes
  back in `index.json` with a `pull_error`, and the other runs are pulled.

- The install page says what to do when `evalkit update` fails with
  `KeyError: 'filename'`: a CLI older than 0.3.0 cannot update itself, so
  run `uv tool install --reinstall "evalkit-py==<version>"` once.
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
