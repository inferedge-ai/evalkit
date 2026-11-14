# Changelog

## Unreleased

### Features

- **Review:** Reviewers can reject a case from the CLI (`evalkit review reject <dataset> <case_id>... --comment "<reason>"`) and API. A rejected case is excluded from runs and scores until re-requested, is not carried to promotion targets, and an active copy in a promotion target is withdrawn on rejection. Shows as `Rejected` with reviewer and reason in `evalkit review show`, `evalkit review queue --rejected`, and the UI cases table.


## 0.6.0

_Released 2026-11-13._ Experiments can take trajectories from the agent's own
trace store, scorecards show errored runs, and evalkit is easier to start
with: `evalkit.yaml` is optional, the span mapping presets ship in the
package and the quickstart is a short tutorial.

### Before you upgrade

- **CLI:** Run `evalkit update --server <url>` once the server is on 0.6.0;
  a 0.5 CLI refuses every other command against it.
- **Server:** Run `evalkit db upgrade` before the server starts. Migration
  `0019` adds `agent_runs.trajectory_note`.
- **Packaging:** The `otel` extra is gone, so drop `[otel]` from your
  `evalkit-py` pins. The OpenTelemetry SDK and the OTLP HTTP exporter are now
  base dependencies; pip and uv warn about the unknown extra.
- **CLI:** `evalkit login` without `--url` logs in to hosted evalkit
  (`https://evalkit.inferedge.com`) instead of asking for a server URL. On a
  self-hosted server, pass `--url <url>`.
- **CLI, SDK:** The `env:` references in `evalkit.yaml` and
  `evalkit run --judge-api-key-env` read only the environment, not a `.env`
  file. Export the variables in your shell. `evalkit serve` still reads the
  `.env` beside `config.yaml`.
- **Extending evalkit:** `UiExtensions` has two new required fields,
  `publicRoutes` and `landingPage` (or `null`), so a package's
  `ui/src/extensions/index.tsx` must set both. Signed-out pages have no
  evalkit bar, so a landing page or public route draws its own header and
  way home.

### Features

- **Tracing:** Experiments can take trajectories from the agent's own trace
  store. Set `--trace-source` and `--project` on an OTel agent
  (`evalkit agents create` or `edit`, or the agent form), and SDK runs and
  harness runs import each run's session from that store, with no
  `tracing.register` and no agent token. New flags `--import-settle`,
  `--import-wait` and `--clear-trace-source`; `evalkit traces import` takes
  the agent's source and project when `--source` and `--project` are left out.
- **Web UI:** Every scorecard shows errored runs: runs that ended without
  completing with an answer get no score, and a case is errored or partly
  errored. The counts show above the bucket cards, on each bucket card, on
  the compare page and in the experiments list, and `evalkit summary`,
  `evalkit status`, `evalkit experiments list` and the SDK's scorecard print
  them. `GET /api/experiments/{id}/scorecard` has new fields for them.
- **Web UI:** `evalkit runs list` and the Runs page show a status and a
  duration for every run, OTel sessions included. The run list API has a new
  field `duration_ms`.
- **CLI, SDK:** Commands and SDK calls that show, create or change an
  experiment, dataset, case, agent or agent run end with `link: <url>`, its
  page in the web UI. `RunSummary` has a new `url` field, and
  `evalkit runs list --json` adds `url` to each run.
- **CLI:** `evalkit.yaml` is optional. Without it, cases are read from
  `evalkit/cases/` and fixtures from `evalkit/fixtures/`, so `evalkit sync`,
  `evalkit run` and `run_experiment` work with only
  `evalkit/cases/<dataset>/` on disk. A file that leaves out `cases` or
  `fixtures` gets the same defaults.
- **Tracing:** The span mapping presets ship in the package. `--mapping` on
  `evalkit agents create` and `edit`, and `evalkit traces mapping push`, take
  a preset name (`openinference`, `gen_ai`, `langchain` and the rest) or a
  file path. The new `evalkit traces mapping presets [NAME]` lists the
  presets or prints one as JSON.
- **Web UI:** The new-agent and edit-agent forms have a preset picker above
  "Span mapping" that fills the field with the preset's JSON, with Undo when
  it replaces your text. `GET /api/mapping-presets` returns the presets.
- **Sign-in:** New setting `auth.oidc.cli_client_id` gives `evalkit login`
  its own OAuth client, for identity providers that skip the approval step
  for the web client. Unset, it defaults to `client_id`. People need
  `evalkit update` before an operator removes the web client's loopback
  redirect.
- **Extending evalkit:** A package can add pages for signed-out visitors
  (`publicRoutes`) and a page for `/` (`landingPage`), link to sign-in with
  `SignInLink`, and pass `prompt` (such as `prompt=create`) through
  `/login?prompt=<value>`. `useOptionalMe`, `useTheme`, `Lockup` and
  `/favicon.svg` are stable names.
- **Extending evalkit:** New setting `auth.oidc.ui_resources` lists other
  APIs the web UI signs in for, and a package's pages get a token for one
  with `getApiToken(resource)`. It needs `authorize_params.resource`; the
  server refuses to start without it.
- **Docs:** A new `getting-started` skill walks a coding agent through the
  quickstart in the developer's agent project, and the SDK reference is a
  page in the web UI's docs. `evalkit skills install` copies both.

### Improvements

- **Docs:** The Quickstart is a short tutorial for hosted evalkit: connect an
  agent through Phoenix or by sending traces, mine cases from its runs, run a
  baseline and let the eval-driven-development skill improve the agent. The
  rest moved to the new page Beyond the quickstart. The Install page installs
  `evalkit-py` into the agent's own project.
- **Docs:** The docs site uses the web UI's colours, fonts and layout, and
  the docs gain tabs, row anchors and images, in the web UI and on the site.
- **Web UI:** A new logo, a `/e` mark beside "evalkit", in the sidebar, on
  the sign-in card, on the docs page, as the favicon and on the docs site.
- **Web UI:** Signed-out pages have no evalkit bar. The docs page has its own
  header with the logo, the theme switch and Sign in. The sign-in page has a
  footer with Docs, GitHub and the server's version, and uses the theme last
  chosen in that browser.
- **Web UI:** Questions, answers, trajectories, workspace files and the docs
  share one Markdown look, with syntax highlighting and a copy button on code
  blocks, tables that scroll sideways and links that open in a new tab.
- **Web UI:** With single sign-on, the web UI gives up on the identity
  provider after 10 seconds at sign-in and refresh instead of hanging.
- **Sign-in:** With `auth.password_login: none`, evalkit makes no password
  accounts: `evalkit users add <name>` with a password and
  `evalkit users set-password` answer 400, and the web UI has no Users page.
  Machine users are added as before.
- **Sign-in:** With `auth.oidc.admin_claim` set, `evalkit users promote`
  answers 400 for a person bound to the identity provider, since their next
  sign-in sets admin from the claim. `evalkit users demote` still works.
- **Evaluators:** Which runs are scored follows the run's status and answer,
  not its error text. A `completed` run with an answer and a non-fatal error
  is now scored, and a run that did not complete with an answer is no longer
  scored on an empty answer. `evalkit evaluate <id>` scores the runs of an
  earlier experiment that were left out.
- **Tracing:** A run whose trajectory is partial or unavailable, or whose
  mapping read no LLM or tool steps, says why: in the experiment's runs, on
  the run page, in `RunSummary.trajectory_note` and on the progress line. A
  session that holds only evalkit's `evalkit.run` span is `unavailable`.
- **SDK:** An SDK run with no tracer provider whose task neither takes
  `metadata` nor returns a session id fails at once with a message naming
  the fix.
- **CLI:** `evalkit skills install` without `--target` writes to both
  `.agents/skills/` and `.claude/skills/`. It skips a folder with an evalkit
  skill's name that it did not install, names it and exits 1; `evalkit update`
  refreshes both folders.
- **CLI:** A sync always looks in the fixtures directory for bytes the server
  does not hold, and fails naming the file it looked for. It refuses cases or
  fixtures inside an `evalkit` folder holding an `__init__.py`, which would
  hide the installed package. `cases:` or `fixtures:` set to an empty value
  fails to load; leave the key out.

### Fixes

- **CLI:** Fixed every `evalkit` command crashing with
  `No module named 'bcrypt'` in a project that has uvicorn and SQLAlchemy
  but not the server extra. The server commands now say to install
  `evalkit-py[server]`.
- **Tracing:** Fixed Phoenix imports seeing no LLM or tool spans with the
  `openinference` mapping when Phoenix returns `openinference.span.kind`
  only in its `span_kind` field.
- **Tracing:** Fixed a span upsert moving `received_at` on an unchanged span
  and an empty session id replacing a stored one.

## 0.5.0

_Released 2026-11-11._ A redesigned web UI, sign-in through your identity
provider alone, and finer control over which private addresses a server may
call.

### Before you upgrade

- **CLI:** Run `evalkit update --server <url>` once the server is on 0.5.0;
  a 0.4 CLI refuses every other command against it.
- **Server:** `GET /api/meta` no longer returns `accent_color`; read it from
  the new public `GET /api/appearance`.
- **Extending evalkit:** `UiExtensions` drops `moreNav` and `adminNav`; move
  those pages to `accountSettings` (everyone) or `organizationSettings`
  (admins), named in lowercase letters, digits and dashes, and not
  `workspace`, `users`, `workspaces`, `trace-sources` or `llm-providers`
  (evalkit drops a page whose name breaks this or is already taken).
- **Extending evalkit:** evalkit's CSS classes for buttons, badges, inputs,
  cards, tabs, form rows, checkboxes and loading placeholders (`btn`, `chip`,
  `input`, `card`, `card-head`, `tab-button`, `form-row`, `form-label`,
  `check`, `skeleton`) are gone, so a page that uses them moves to the shadcn
  components. Dark mode is the `dark` class on `<html>` instead of
  `data-theme="dark"`.

### Features

- **Sign-in:** `auth.password_login: none` sends a signed-out visitor
  straight to your identity provider, refuses every password and needs no
  `EVALKIT_ADMIN_PASSWORD`. It requires `auth.oidc.admin_claim`, so the
  provider says who is an admin, and signing out stops every tab at
  **Sign in again** rather than signing you straight back in.
- **Server:** `trusted_endpoints` lists `http` or `https` origins that every
  private-address check lets through, for a service every tenant may reach,
  such as an in-cluster LLM gateway that checks its own credentials.
- **Web UI:** A Workspaces page (Settings › Organization) lists every
  workspace with its member count and promotion link, and admins create
  shared workspaces there. `GET /api/workspaces` returns `members`.
- **Extending evalkit:** A package serving evalkit can decide the
  private-address rule per request, for example per tenant, by replacing
  `get_allow_private_endpoints` from `evalkit.api.deps`.

### Improvements

- **Web UI:** A new design: a left sidebar that collapses to icons (⌘B) and
  opens as a sheet on a phone, Roboto at 14px in new light and dark colours,
  and new buttons, forms, dialogs and icons. Every page is laid out again,
  with loading placeholders shaped like what loads, empty pages that say what
  to run, and errors shown where the data failed.
- **Web UI:** One Settings area, opened from the account menu, holds the
  current workspace's settings and, for admins, Users, Workspaces, Trace
  sources and LLM providers. Old URLs redirect.
- **Web UI:** Tables let you resize, reorder, sort and hide columns and
  filter text columns, and each table remembers its layout in the browser.
  Layouts saved before 0.5.0 are dropped once.
- **Web UI:** A page you come back to shows its last data at once and
  refreshes in the background, and a save, archive or comment shows without
  a reload.
- **Web UI:** A deploy's `server.accent_color` draws a strip along the top of
  every page, sign-in included, instead of recolouring buttons and links.
- **Web UI:** The reviews queue you picked is in the URL
  (`/reviews?view=all`), so Back from a case returns to it.
- **Sign-in:** A first SSO sign-in whose name another SSO user, an archived
  account or a workspace already has gets the next free number (`ann1`,
  `ann2`) instead of a 409. A name held by an active password account not yet
  linked to SSO still gets the 409, which names the subject for
  `evalkit users bind`.
- **Sign-in:** Usernames match in any case: `evalkit users add Bob` creates
  `bob`, and the password login and the user and member commands accept
  `Bob`.

### Fixes

- **Sign-in:** Fixed a first SSO sign-in refusing anyone whose email or
  `preferred_username` has a `+` or another character outside lowercase
  letters, digits, `.`, `_` and `-`. Usernames now take any character except
  whitespace, `@`, `/`, `\`, `?`, `#` and `%`, and can't be just `.` or `..`.
- **Sign-in:** Fixed a signed-out visitor or an expired password session
  getting an error instead of the login page while the identity provider is
  unreachable, so an admin can still sign in with a password.
- **CLI:** Fixed `evalkit lint` and `evalkit sync` refusing a case file with
  top-level `x-` keys (such as `x-shared`, to hold YAML anchors), broken in
  0.4.0. The server parses these files, so the fix needs a 0.5.0 server.
- **Web UI:** Fixed a file on a run's Workspace tab failing to load with no
  reason, or just `Error`: it shows the server's message, or `HTTP <status>`.
  The run file endpoints answer 502 `harness unreachable` instead of a
  plain-text 500 when the harness is down.
- **Web UI:** Fixed an API error with no message, such as a proxy's 502
  page, showing as an empty error; it shows `HTTP <status>`.
- **Web UI:** Fixed a score named like a JavaScript object property (such as
  `constructor`) breaking the page that shows its ⓘ.
- **Web UI:** Fixed GHSA-238p-pmpm-9mq7 (KaTeX below 0.18.2 reads polluted
  object prototypes) by using KaTeX 0.19.1 throughout.
- **Extending evalkit:** Fixed trace import checking private addresses
  against the server's own config instead of a package's replaced
  `get_config`.

## 0.4.0

_Released 2026-11-07._ Single sign-on through any OIDC identity provider,
judge-using evaluators scored on the server, packages that extend evalkit,
and an Apache-2.0 licence (was AGPL-3.0).

### Before you upgrade

- **CLI:** Run `evalkit update --server <url>` once the server is on 0.4.0;
  a 0.3 CLI refuses every other command against it.
- **Server:** Stop the old server, then run `evalkit db upgrade` before
  starting the new one. One migration fails if the default judge's base URL
  matches no LLM provider: register one or clear the default judge first.
- **Server:** Each server now opens up to 30 database connections
  (`db.pool_size` 20 plus `db.max_overflow` 10). Keep that times your
  replicas well under Postgres `max_connections`, or lower them in
  `config.yaml`.
- **Server:** The server config no longer takes `mcp.oauth`; a config that
  has it fails to load. Configure `auth.oidc` instead.
- **Server:** Trace sources are checked against private addresses like LLM
  providers. Without `allow_private_endpoints: true`, a source on a private
  address stops importing until an admin points it at a public one.
- **Server:** A proxy in front of the server must let
  `POST /api/experiment-runs/<id>/server-evaluations/<name>` run for
  minutes; the CLI and SDK wait up to 900 s
  (`EVALKIT_SERVER_EVALUATOR_TIMEOUT`).
- **Server:** A harness that declares `run_listing` names the tenant
  `org_id` instead of `client_id`, in the `GET /runs` filter and on each
  run. The run browser's filter and the MCP `find_runs` tool say `org_id`
  too.
- **Evaluators:** The default judge is set by provider name: `GET`, `PUT`
  and `PATCH /api/judge-default` use `provider`, and a `base_url` judge is
  refused there and on server runs.
- **Evaluators:** `evalkit judge-default set --concurrency` is gone and
  `Judge(concurrency=...)` is ignored; set `evaluation_concurrency` instead.
- **SDK:** `evalkit.yaml` no longer takes `credentials.llm_providers` (or
  `credentials.judges`); a file that has it fails to load. Pass the judge key
  with `--judge-api-key-env` or `Judge(api_key=...)`.
- **SDK:** "Split" is "tag" everywhere: `run_experiment(split="x")` is
  `run_experiment(tags=["x"])`, the API's `selection` takes `tags` (a request
  with `tag` is refused), and `GET /api/datasets/{ref}/splits` is
  `GET /api/datasets/{ref}/tags`.
- **SDK:** Code that embeds `evalkit.sdk` installs `httpx2` instead of
  `httpx`, and `SyncApi` and `AsyncApi` take an `httpx2.Auth`
  (`evalkit.client.oidc.static_auth(token)` wraps a token).
- **CLI:** `evalkit run finance/<name>` no longer falls back to a tag when
  `<name>` is not a folder or file of the dataset: pass `--tag <name>`.
- **CLI:** `evalkit traces mapping push --activate` is gone, and so is the
  `activate` query parameter on `PUT /api/agents/{agent}/mappings/draft`:
  push the draft, then run `evalkit traces mapping activate <agent>
  <version>`.
- **CLI:** A case file with `cases:` or `overlays:` refuses any other
  top-level key besides `defaults`, where it used to ignore it (a typo such
  as `defualts:` dropped your defaults silently), and both must be lists.
- **Tracing:** `tracing.register`, `tracing.exporter` and
  `tracing.processor` need `EVALKIT_OTEL_TOKEN` (an agent token); they no
  longer fall back to a login token.
- **Tracing:** Sibling agents that hand over without a tool call (CrewAI
  sequential tasks, say) now make one trajectory; list them in the mapping's
  `subagent_spans` to keep them apart.
- **Packaging:** Only releases are published to PyPI; get a `develop` build
  (`X.Y.Z.devN`) from the workflow artifact or the GHCR image.
- **Extending evalkit:** `create_app` no longer takes `mcp_jwt_verifier`;
  `/mcp` verifies tokens with the `auth.oidc` settings.

### Features

- **Sign-in:** Single sign-on through any OIDC identity provider that
  issues JWT access tokens (`auth.oidc`): the web UI offers "Continue with
  <provider>", `evalkit login` opens the browser (or uses the device flow
  with `--no-browser`), and the first sign-in creates the user.
  `auth.password_login` (`admin` or `all`) decides who may still use a
  password.
- **Sign-in:** CI signs in as a machine client with `EVALKIT_OIDC_CLIENT_ID`
  and `EVALKIT_OIDC_CLIENT_SECRET`; an admin adds it with
  `evalkit users add <name> --subject <sub>`, and `evalkit users bind` links
  an existing user to an identity.
- **Sign-in:** `auth.oidc.admin_claim` lets the identity provider grant and
  revoke evalkit admin at each sign-in, and `auth.oidc.required_scopes`
  refuses access tokens that lack a scope.
- **Evaluators:** Local and SDK runs score the judge-using built-ins
  (`answer_rubrics`, `presentation_judge`, `trajectory_rubrics`) on the
  server with its LLM provider, so they need no judge key; each score
  records where it ran. To bring your own judge, pass `--judge-base-url` and
  `--judge-api-key-env`, or `Judge(base_url=..., api_key=...)`.
- **Extending evalkit:** Another package can serve evalkit with API routes
  of its own (`app.include_router()` after `create_app`, behind sign-in with
  `SIGNED_IN`) and UI pages of its own (`ui/src/extensions/`), and can split
  the data by organisation with `create_app(resolve_tenant=...)`, an async
  function that returns the tenant id or `None` to refuse the request. See
  "Extending evalkit" in `AGENTS.md`.
- **CLI:** `evalkit run --tag` takes several tags and runs the cases
  carrying any of them; the run dialog's Tags is a multi-select, and an
  experiment's case table filters by tag.
- **CLI:** `evalkit traces mapping activate <agent> <version>` makes a
  pushed draft live, or rolls back to an older version.
- **CLI:** New commands `evalkit experiments list` and
  `evalkit datasets show <dataset>`: the first filters by `--agent`,
  `--dataset` and `--limit`; the second prints a dataset's last sync, case
  counts per review state and tags.
- **Review:** `evalkit review approve` takes several cases or
  `--all-waiting`, `evalkit review queue finance@dev` lists one dataset's
  cases, and `evalkit review resolve <comment-id>` needs only the comment id.
- **Web UI:** Datasets in a personal workspace have Run experiment too; set
  the options and copy the `evalkit run` command it builds.
- **SDK:** A code-driven agent can set its own input schema with
  `evalkit agents create|edit <name> --input-schema schema.json`.
- **Docs:** New pages: Framework mappings (a ready-to-use span mapping for
  ten agent frameworks), Server config (every key and its default), Case
  schema, and CI sync (lint cases on PRs, sync on merge). Docs pages in the
  web UI need no sign-in.

### Improvements

- **CLI:** `evalkit summary <id> --baseline <id>` shows the change in every
  bucket, evaluator and gauge, and lists improved, regressed, new and
  removed cases. Its failing cases include any case where one evaluator
  scored below 0.80.
- **Tracing:** Trajectories built from OTel spans fit more agent shapes:
  subagents only where a tool started them, a user step for every message in
  a chat, and mapping rules `rekind_spans`, `main_agent` and
  `subagent_spans`. `evalkit traces check` and the SDK dry run warn when a
  trajectory looks wrong: no answer span, LLM spans that lost attributes,
  tool spans without tool calls, messages with an unknown role.
- **Evaluators:** Scoring runs at most 16 runs at once; change it with
  `--evaluation-concurrency` on `evalkit run` and `evalkit evaluate`, in the
  SDK, or in the run dialog. Server runs take `runner.evaluation_concurrency`
  from the server config.
- **CLI:** `evalkit lint` errors name the file, the case and the valid keys,
  say where a misplaced evaluator belongs, and cover every broken case in a
  file. `evalkit lint --offline` reports them as findings instead of a
  traceback.
- **Review:** The review queue names the author a case went back to,
  `evalkit sync` says edited cases are back in review, `review request`
  skips cases already waiting on that reviewer, and a promoted case's
  changelog includes its history from the source workspace.
- **CLI:** `evalkit datasets list` and `evalkit review queue` take
  `--workspace`, and `evalkit workspaces list` shows promotion links.
- **CLI:** `evalkit init-server --port` sets the server's port, and
  `evalkit serve` defaults to the port in `server.base_url`.
- **Packaging:** evalkit runs on Python 3.12 as well as 3.13.
- **Server:** A page path returns the web UI only to a browser
  (`Accept: text/html`); other clients get 404.
- **Docs:** The docs site root serves the latest release's pages directly,
  and the Install page covers running the CLI inside a uv project and
  installing the skills.

### Fixes

- **Evaluators:** Fixed scoring an experiment of more than about 40 runs
  failing with 500s: each scoring request uses one database connection, and
  a server out of connections answers 503 with `Retry-After`, which the CLI
  and SDK retry.
- **Evaluators:** Fixed a re-score treating an evaluator that errored after
  an earlier success as done; it retries it.
- **Evaluators:** Fixed an evaluator that raised an exception without a
  message recording an empty error; it records the exception type, such as
  `ReadTimeout`.
- **Tracing:** Fixed Google ADK, Gemini and LangChain-style traces losing
  the model's messages and tool calls (roles such as `model`, `ai` and
  `human`), which made tool evaluators score 0.
- **Tracing:** Fixed an agent behind FastAPI or another instrumented HTTP
  server losing its spans when the server's request span is the trace root,
  once the session id is on the agent's own turn span.
- **CLI:** Fixed `evalkit runs pull` failing the whole batch with a 500 when
  one run's trajectory is missing from the blob store; that run gets a
  `pull_error` and the others are pulled. Every `run.json` has a
  `started_at` field, `null` when the harness gives no creation time.
- **CLI:** Fixed `evalkit lint --agent` failing with an `AssertionError` on a
  code-driven agent.
- **Server:** Fixed a malformed URL in an agent, evaluator, LLM provider or
  trace source answering a server error instead of 400 when
  `allow_private_endpoints` is off.
- **Server:** Fixed an MCP client probing `/.well-known/` on a server
  without SSO getting the web UI's page.

## 0.3.0

_Released 2026-10-30._ evalkit is on PyPI as `evalkit-py`, a server is one
`pip install` away, and API keys live in the database.

### Before you upgrade

- **CLI:** A CLI older than 0.3.0 can't update itself: run
  `uv tool install --reinstall "evalkit-py==<version>"` once, with the
  version `curl <server>/api/cli` prints. A project with a vendored wheel
  moves once: `uv remove evalkit && uv add "evalkit-py[otel]==<version>"`.
- **Server:** Remove `credentials:` and `judge_default:` from the server
  config; a server that still has them refuses to start and prints the
  commands that set the same values. Start it, then run those commands with
  each key in your shell.
- **Server:** Run `evalkit db upgrade` for migration 0013.
- **Server:** Shared agent, evaluator and LLM provider URLs on a private,
  loopback or link-local address are refused. Set
  `allow_private_endpoints: true` when they run in the same cluster or on
  `host.docker.internal`.
- **Server:** Server runs, resumes and re-evaluations judge only with a
  registered LLM provider; another base URL is refused with 400. Register
  one with `evalkit llm-providers create`.
- **Server:** The server no longer serves its own wheel:
  `/api/cli/wheel`, `/api/cli/simple` and `/api/cli/files/...` are gone,
  `GET /api/cli` returns only `version` and `package`, `EVALKIT_WHEEL_DIR`
  is ignored and `evalkit update --vendor` is removed.
- **SDK:** In `evalkit.yaml`, `credentials.judges` is now
  `credentials.llm_providers`.
- **CLI:** `evalkit trace-sources rekey` is now `evalkit db rekey`, and
  re-encrypts every stored key.

### Features

- **Packaging:** evalkit is on PyPI as `evalkit-py` (the command and module
  are still `evalkit`). `evalkit update` installs the server's version and
  moves an `evalkit-py==...` pin in `./pyproject.toml`.
- **Server:** `pip install "evalkit-py[server]"` installs a working server,
  web UI included, and `evalkit init-server` sets one up against a Postgres
  you already run: it creates the database, writes `config.yaml` and `.env`,
  migrates, and registers your LLM as the default judge.
- **Server:** API keys and the default judge are stored in the database, so
  adding an agent, evaluator or LLM provider needs no restart; keys are
  encrypted and never returned. Set keys with `--api-key-env` on
  `evalkit agents` and `evalkit evaluators`, providers with
  `evalkit llm-providers`, and the judge with `evalkit judge-default set`.
- **Server:** The server reports errors to Sentry when the config has a
  `sentry:` block.
- **Docs:** The docs and an SDK API reference are published at
  https://inferedge-ai.github.io/evalkit/, one version per release plus
  `dev`.
- **CLI:** New `evalkit evaluators show` and `evalkit evaluators restore`;
  the web UI can edit an evaluator and restore an archived one.

### Improvements

- **Server:** Writers of a shared workspace can set an agent's harness URL
  and an evaluator's endpoint, not only admins. Changing a URL drops the
  stored key unless the same call sets a new one.
- **Server:** A server run whose agent, evaluator or provider has no key
  calls without one and lists `warnings`, instead of failing with 400.
- **Server:** `evalkit serve` and `evalkit db upgrade` read a `.env` beside
  `config.yaml`; variables already in the environment win.

## 0.2.0

_Released 2026-10-27._ Review no longer decides which cases run, and
experiment lists show only the scores they have.

### Before you upgrade

- **CLI:** Run `evalkit update --server <url>` once the server is on 0.2.0;
  a 0.1 CLI refuses every other command against it.
- **CLI:** `--include-drafts` and `include_drafts=` are removed; drop them
  from scripts and CI, or the command fails.
- **Review:** The Draft review state is now Unreviewed: `unreviewed` in the
  API and `evalkit datasets list --state`, and the sync report's
  `review.now_draft` is `review.now_unreviewed`.

### Improvements

- **Review:** `evalkit run`, `run_experiment` and the run dialog run every
  selected case of a shared dataset, whatever its review state; review
  decides what gets promoted.
- **CLI:** `evalkit lint <dir>` checks fixture keys like `--offline` does,
  so CI can drop its separate offline lint.
- **Web UI:** Experiment lists show a score column only for buckets some
  experiment was scored on, plus `eff` and `custom` when present.
- **Web UI:** An experiment shows its judge model and reasoning effort, and
  experiment lists have an optional `judge` column.
- **Web UI:** The Agents page lists archived agents under Archived (N), so
  you can open one and restore it.
- **Web UI:** An empty workspace you can't write to says it's read-only
  instead of suggesting `evalkit run` or `evalkit sync`.

### Fixes

- **Web UI:** Fixed Reviews and Runs keeping an old error after a later
  load succeeds.
- **CLI:** Fixed `evalkit traces check` on an unknown agent not saying how
  to create it.
- **Sign-in:** Fixed OAuth clients such as Claude failing to connect to
  `/mcp` with `mcp.oauth` set, because the protected-resource metadata added
  a trailing slash to the issuer.

## 0.1.0

_Released 2026-10-26._ The first public release of evalkit, a self-hosted
system for improving AI agents: mine failures from real runs into eval
cases, have domain experts review them, run them against your agent, and fix
what fails. See the [README](README.md) to self-host it or to connect to an
existing server.
