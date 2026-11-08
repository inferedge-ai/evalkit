# Changelog

## Unreleased

- The web UI uses InferEdge's design tokens and shadcn's setup
  (`ui/components.json`, the `@/` import alias): Roboto at 14px (was the
  system font at 13px), new light and dark colours with a white page in light
  mode and slightly darker borders, and a larger corner radius (`rounded-md`
  is 8px, was 6px). Dark mode is the `dark` class on `<html>` instead of
  `data-theme="dark"`. UI tests can render components with jsdom and Testing
  Library.
- A deploy's `server.accent_color` no longer recolours the web UI's buttons and
  links, which stay the brand blue. It draws a 3px strip along the top of
  every page, the login page included. The colour comes from the new public
  `GET /api/appearance` (`{"accent_color": ...}`), and `GET /api/meta` no
  longer returns `accent_color`. Before, the colour loaded only after sign-in,
  so the login page was blue and signed-in pages took the accent.
- The web UI header shows only "Evalkit"; the InferEdge lockup beside it is
  gone.
- `ui/package.json` overrides the KaTeX that `rehype-katex` and `remark-math`
  bring in (0.16.47) with the UI's own KaTeX (0.19.1), which fixes
  GHSA-238p-pmpm-9mq7 (KaTeX below 0.18.2 can read polluted object
  prototypes).
- The web UI loads its data with TanStack Query (`ui/src/lib/query.ts`). A
  page you come back to shows its last data at once and refreshes it in the
  background. After a save, archive, comment or other change, every page on
  screen refetches, so the change shows without a reload. A running
  experiment's page and the trace import panel still poll until they finish,
  and pause while the browser tab is in the background. Failed loads are not
  retried. Signing in clears everything loaded earlier in the tab, so the next
  user never sees the previous user's pages.
- The web UI's forms for launching a run, creating or editing an agent, a
  remote evaluator or an LLM provider open in a shadcn dialog: focus stays
  inside it, and Esc or a click outside closes it without saving. Archiving an
  agent and re-evaluating an experiment whose runs have not all completed ask
  in a confirmation dialog instead of the browser's `confirm()` box. The UI
  gains shadcn's components under `ui/src/components/ui/` and the
  `@base-ui/react`, `class-variance-authority` and `lucide-react`
  dependencies.

## 0.4.0

- evalkit is now licensed under Apache-2.0 instead of AGPL-3.0 (`LICENSE`).
- The server serves the web UI with FastAPI's `app.frontend()` instead of a
  catch-all route, so API routes another package adds with
  `app.include_router()` after `create_app` answer GET.
  `evalkit.api.auth.SIGNED_IN` puts such a route behind evalkit's sign-in. The
  web UI takes extra pages and nav items from `ui/src/extensions/`, empty in
  evalkit. See "Extending evalkit" in `AGENTS.md`. Responses that change:
  - A page path such as `/datasets` returns the UI only to a browser
    navigating to it (`Accept: text/html`); other clients get 404. `/`
    returns it to every client, and `HEAD /` answers 200 instead of 405.
  - A request to an unknown path with a method other than GET or HEAD
    answers 404 instead of 405, `/api` paths included. A GET to an API path
    that only takes other methods (`GET /api/auth/login`) answers 405
    instead of 404.
  - A path that differs from an API route only by a trailing slash
    (`/api/agents/`) gets a 307 redirect to the route instead of 404 or 405.
  - A page path containing an encoded NUL byte answers 404 instead of 500.
- New docs page, Framework mappings (`docs/user/framework-mappings.md`): a
  ready-to-use span mapping per agent framework (LangGraph, LangChain,
  CrewAI, Agno, OpenAI Agents SDK, LlamaIndex, Google ADK, Pydantic AI,
  Claude Agent SDK, Mastra), with the agent-side changes a mapping cannot
  make. The `connect-traces` skill ships the files under `references/`
  (`langchain.json`, `crewai.json`, `agno.json`, `llamaindex.json`,
  `google-adk.json`, `claude-agent-sdk.json`, `mastra.json`) and tells
  coding agents to pick the framework's file first.
- `evalkit traces check` (and the SDK's dry run) warns on a new `answer`
  line when the mapping's `answer` rule names a span the session does not
  have, for example a copied `<agent name>` placeholder or a typo. Before,
  the trajectory silently had no answer. The trajectory's `extra` carries
  the name as `answer_span_missing`.
- An evaluator that raises an exception without a message (an httpx
  timeout, say) now records the exception type, e.g. `ReadTimeout`,
  instead of an empty error, in the CLI, `evaluations.json` and the
  server log. Builtin, remote and Python evaluators all do this.
- A re-score (`evalkit evaluate`, SDK `evaluate_experiment`) now retries
  an evaluator that errored after an earlier success. Before, the old
  success rows stayed next to the error row, so the evaluator counted as
  done and the error never cleared. An evaluator with any row marked as an
  error (an `error` or the label `error`) is never done. Publishing an
  evaluator's rows now replaces every row it wrote for that run.
- The collector stores each span's `dropped_attributes_count` (the
  attributes the OTel SDK dropped past its limit) in a new `spans` column
  (migration 0018). `GET /api/agents/<ref>/sessions/<id>/spans` returns it,
  ESF spans carry it as `dropped_attributes`, and `evalkit traces check`
  warns on its `llm` line when an LLM span lost attributes, which usually
  means lost messages. The Tracing page and the connect-traces skill explain
  how to raise `OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT` (default 128).
- The collector now gives each span the session of its outermost ancestor
  that carries one, not only the trace root's. An agent behind FastAPI or
  another instrumented HTTP server no longer loses its spans when the
  server's request span (`POST /chat`) is the root: put the session id on
  your own turn span and the agent's spans join it. A root span with a
  session still sets the session for the whole trace. `evalkit traces check`
  now warns, instead of failing, when the trace root is outside the session,
  and names the top span. The Tracing page has an "Agent behind an HTTP
  server" recipe.
- Local and SDK runs now score the judge-using built-in evaluators
  (`answer_rubrics`, `presentation_judge`, `trajectory_rubrics`) on the evalkit
  server by default, with the server's LLM provider and its stored key. You no
  longer need a judge key in `evalkit.yaml` for these runs.
  - A judge now names exactly one of `provider` (a registered LLM provider, by
    name) or `base_url`. `Judge(provider="openai", model="gpt-5")` or
    `evalkit run --judge-provider openai` picks another provider, still scored
    on the server.
  - To bring your own judge, pass `Judge(base_url=..., model=..., api_key=...)`
    or `evalkit run --judge-base-url URL --judge-api-key-env NAME` (the env
    var or `.env` entry holding the key); those evaluators then run on your
    machine. The key is never stored, so re-scoring and resuming need it
    again: `evalkit evaluate <id> --judge-api-key-env NAME`,
    `evalkit run … --resume <id> --judge-api-key-env NAME`, or
    `evaluate_experiment(..., judge=Judge(...))` with the same judge (a
    different judge is refused). `--judge-provider` with
    `--judge-base-url` is refused, and so is `--judge-base-url` without
    `--judge-api-key-env`.
  - `credentials.llm_providers` is gone from `evalkit.yaml`: a file that still
    has it (or `credentials.judges`) fails to load. Move the key to
    `--judge-api-key-env` or `Judge(api_key=...)`.
  - `tool_trajectory_match`, `required_tools_called` and `max_response_time`
    always run on your machine.
  - Each evaluation records `scored_on` (`local` or `server`); the run page
    shows it beside each score.
  - New endpoints: `POST /api/experiment-runs/<id>/server-evaluations/<name>`
    (for the CLI and SDK; only users who may change the experiment) and
    `GET /api/builtin-evaluators`. A proxy in front of the server must let
    the server-evaluations call run for minutes; the CLI and SDK wait up to
    900 s (`EVALKIT_SERVER_EVALUATOR_TIMEOUT`).
  - The default judge is stored by provider name: `GET`/`PUT`/`PATCH
    /api/judge-default` use `provider`, and a `base_url` is refused. Server
    runs refuse a `base_url` judge. Changing the default judge's provider URL
    is now allowed; archiving it is still refused.
  - Starting an experiment with no judge and no default judge is refused only
    when a selected case uses a judge-using evaluator.
  - A remote evaluator can no longer take a built-in's name.
  - The workspace Evaluators page lists the built-in evaluators.
  - Migration `0017` adds `evaluations.scored_on` (`server` for server runs,
    that is shared agents with a harness URL; `local` otherwise, including SDK
    runs of shared code-driven agents) and rewrites the default judge, and server runs'
    recorded judges, from base URL to provider name. It fails if the default
    judge's base URL matches no provider: register one or clear the default
    judge first.

- The web UI's docs pages (`/docs/...`, `/cli`) no longer need a sign-in.
  Signed-out visitors see them under a public navbar (logo, Docs, theme
  toggle, Sign in); signed-in users still see them inside the full app. The
  login page now carries the same public navbar, with a link to the docs.
- Trace source URLs are now checked like LLM provider URLs. Without
  `allow_private_endpoints: true`, `POST /api/trace-sources` and
  `PATCH /api/trace-sources/<name>` (`evalkit trace-sources create` and
  `edit --url`) answer 400 when the URL resolves to a loopback, private,
  link-local or reserved address, or does not resolve. Editing only the key
  is not checked. Importing from a source whose stored URL is such an address
  (`evalkit traces import`, `POST /api/agents/<ref>/imports` and
  `evalkit traces imports <agent> <id> --continue`) answers the same 400 and
  fetches nothing, so a source saved before this release stops importing until
  an admin points it at a public address.
- With `allow_private_endpoints` off, a malformed URL in an agent, evaluator,
  LLM provider or trace source now answers 400 instead of a server error:
  `<url> is not a valid URL: …` when it cannot be parsed (`http://[::1`), and
  `<url>: cannot resolve <host>` when its host name has an empty or overlong
  label (`http://a..b.com/`).
- `auth.oidc.admin_claim` (`name`, `value`): when set, every SSO sign-in
  sets the person's admin flag from the access token (equal to the value, or
  a list containing it), so the IdP grants and revokes evalkit admin. Unset,
  sign-in never changes the flag, as before.
- `create_app(resolve_tenant=...)` is now async, returns the tenant id as
  text, and may return `None`: the request then answers 403 `no tenant for
  this request` on `/api`, the collector, registration and password login,
  and `/mcp` refuses the token (401), as it does a tenant id that isn't 1-64
  letters, digits, `-` or `_`. Without a wrapper every request is in
  `default`, as before.
- `auth.oidc.required_scopes` (default empty) makes the server refuse IdP
  access tokens that lack a scope, read from the `scope` and `scp` claims:
  `POST /api/auth/oidc/register` (no user is created) and every `/api` request
  answer 403 `access token lacks required scope <scope>`, and `/mcp` answers
  403 `insufficient_scope`. Password
  login and agent tokens are not checked. Every required scope must also be in
  `auth.oidc.scopes`, or the config fails to load. `GET /api/auth/providers`
  lists them as `required_scopes`, and the CLI sends them as `scope` on the
  client credentials request. `/mcp` no longer stamps `evalkit:read`
  on every token it accepts, and its tools no longer carry a per-tool scope.
- evalkit now uses `httpx2` instead of `httpx` and `httpx-retries`. The
  CLI, the SDK and the server make their HTTP calls through `httpx2`, with a
  small retry transport of evalkit's own (`evalkit.client.retry`). Code that
  embeds `evalkit.sdk` now installs `httpx2`, not `httpx`. A request whose
  body is a one-shot stream is now sent once and never retried (the old
  library retried it, and the second send of a spent stream failed). The
  try-it stack's sample agent (`try/agent.py`) and the SDK-runs doc sample
  import `httpx2`.
- The web UI signs in through the configured identity provider: the login
  page shows "Continue with <name>" when the server has `auth.oidc`, with an
  "Admin sign-in" link to the password form (or the form open under the
  button when `auth.password_login` is `all`). The new `/login/callback`
  route completes the sign-in. The account chip shows the SSO email under
  the username, and sign out clears the SSO session locally. The IdP must
  allow evalkit's origin through CORS.
- The UI's history fallback answers 404 for `/.well-known/*`, so an MCP client
  probing `/.well-known/oauth-protected-resource/mcp` on a server without SSO
  no longer gets the UI's `index.html`.
- When `auth.oidc` is set, the `Content-Security-Policy` `connect-src` also allows
  the origins of the IdP's issuer and its token endpoint, so the web UI can
  sign in. Without `auth.oidc` the header is unchanged.
- The database gains a `tenant_id` on `users`, `workspaces`, `cases`,
  `llm_providers`, `trace_sources` and `fixture_blobs`: text, the
  organisation's id, `default` for existing rows and for a server without a
  multi-org wrapper. Usernames, workspace names, case ids, provider names and
  trace source names are now unique per tenant. evalkit keeps no tenants
  table. The default judge moved from `server_settings` to a new
  `tenant_settings` table keyed by tenant id. Migration `0016`; no behaviour
  change for a single installation, and its blob layout is unchanged (only
  tenants other than `default` store objects under `tenants/<id>/`). Stop the
  old server before `evalkit db upgrade`: 0016 drops `server_settings`.
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
  `email`; `users promote` refuses a machine user. If only one of the two
  variables is set, the CLI now stops with an error naming the missing one
  instead of falling back to `EVALKIT_TOKEN`.
- `/mcp` verifies tokens with the same settings as `/api`; a token for the
  configured audience or for `<base_url>/mcp` is its evalkit user, and
  archiving the user cuts off MCP. `mcp.oauth` is removed: a config that
  still has it fails to load and names `auth.oidc`. `create_app` no longer
  takes `mcp_jwt_verifier`. While the identity provider is unreachable,
  `/mcp` answers 503 (as `/api` does) instead of 401, so MCP clients keep
  their tokens and retry. The token's expiry now comes from its `exp` claim.
- During `evalkit login`, a browser callback with the wrong `state` is
  answered with 400 and ignored; the login keeps waiting for the real
  callback until it times out. A body from the identity provider that is
  not valid discovery or JWKS JSON counts as the provider being unreachable.
- `tracing.register`, `tracing.exporter` and `tracing.processor` require
  `EVALKIT_OTEL_TOKEN` (an agent token); the fallback to a login token is
  removed.
- `SyncApi` and `AsyncApi` take an `httpx2.Auth` instead of a token string;
  `evalkit.client.oidc.static_auth(token)` wraps a token.
- Dependencies: `authlib` (with `joserfc` and `cryptography`) is now a base
  dependency; `pyjwt[crypto]` is in the `server` extra.
- Only releases (`X.Y.Z` from `main`) are published to PyPI. `develop`
  builds (`X.Y.Z.devN`) are no longer on PyPI; get them from the workflow
  artifact or the GHCR image.
- `evalkit summary <id> --baseline <id>` now prints a `Δmean` for each
  gauge (tokens, cost, duration, tool calls). Cases that ran in the
  baseline but not in the newer experiment (a `--tag` or `--case-id` run)
  are counted on one line, "not run here (in the baseline only)", instead
  of being listed as "removed".
- New `evalkit experiments list [--agent A] [--dataset D] [--limit N]`
  prints id, dataset, agent, status, start time and bucket scores, newest
  first. `GET /api/experiments` takes `agent` and `dataset` filters.
- `evalkit review approve` takes several case ids, or `--all-waiting` for
  every case in the dataset waiting on you.
- `evalkit review queue` takes a dataset (`evalkit review queue
  finance@dev`) to list only that dataset's cases. `GET /api/review/queue`
  takes a `dataset` filter.
- `evalkit review resolve <comment-id>` resolves a comment by its id alone;
  the older `resolve <dataset> <case> <comment-id>` form still works. New
  `PATCH /api/review/comments/{comment_id}`.
- `evalkit review show` prints the case's evaluators without filled-in
  defaults (`source: null`, `args_mode: subset`, ...), closer to what the
  author wrote. Its changelog, and the case page's, include the promotion
  source's events for a case in a promotion target, limited to the
  versions promoted there, so a promoted case shows its sync and review
  history from `@dev`, not only the promotion.
- The Reviews page in a personal workspace lists the queue across every
  workspace instead of "Personal datasets have no review state".

- A code-driven agent can set its own input schema:
  `evalkit agents create|edit <name> --input-schema schema.json`, and
  `evalkit agents edit <name> --clear-input-schema` goes back to the
  default (an object with a required string `question`). `agents show`
  prints the schema, or the default. An agent with a `harness_url` is
  refused: its harness declares the schema in `GET /capabilities`.
  `run_experiment(dry_run=...)` now checks case inputs against the schema,
  as the real run does. `evalkit lint --agent` on a code-driven agent lints
  against its schema instead of failing with an `AssertionError`. The Case
  schema, Harness API and SDK runs pages document the default schema and
  how each kind of agent sets its own.
- New docs page "Server config" lists every key of the server's
  `config.yaml` with its default, including the database pool
  (`db.pool_size` 20, `db.max_overflow` 10, `db.pool_timeout_seconds` 5) and
  `runner.evaluation_concurrency` (16), and how to size them against
  Postgres `max_connections`. `evalkit init-server` now writes
  `db.pool_size: 5`, `db.max_overflow: 5` and
  `runner.evaluation_concurrency: 4` into the `config.yaml` it creates, with
  a comment naming the server defaults, so several local servers can share
  one Postgres.
- The Install page shows how to run the CLI from inside a uv project
  (`uv add "evalkit-py==…"`, then `uv run evalkit`), how to get the
  skills (`evalkit skills install`, and where the span mappings land), and
  that `skills install` records its target in `~/.evalkit/skills-targets`.
  The server install lines on Install and Try locally now pin the version,
  and Workspaces shows `evalkit users add`.
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
