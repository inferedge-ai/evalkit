---
name: evalkit-deployment
description: Deploys evalkit for a client and integrates their agent — deploy the server image, run migrations, implement the Harness API (with ATIF trajectory conversion) and any Custom Evaluator API endpoints, write config.yaml, sync a first suite, wire lint/sync into CI, verify with a live harness check. Use when trying evalkit on one machine with the try-it stack, standing up evalkit for the first time, upgrading a deployment, or adding a new agent or custom evaluator. Not for everyday CLI use — see evalkit-usage.
---

# evalkit deployment

One-time work done by the developer who stands evalkit up inside the client's
infrastructure. Walk it from zero to a passing `evalkit harness check`. Work
through the steps in order; each is verifiable before the next. Command syntax
is documented by the CLI itself — run `evalkit --help` (and `--help` on any
subcommand) instead of guessing flags.

Everyday use of the deployed server — installing the CLI, logging in, syncing
cases, running experiments, reviewing cases — is the **evalkit-usage** skill.
Every developer on the client's team needs that one; only the deployer needs
this one. You need no CLI before the server is up: the image contains one
(`docker compose run --rm server evalkit …`). Once the server runs, install
it on your machine from the server, like everyone after you:
`uv tool install --reinstall evalkit --index <server url>/api/cli/simple`
(see evalkit-usage).

`evalkit skills install` puts the evalkit user docs in `evalkit-docs/`
beside this skill; links to `../evalkit-docs/` point there. In the evalkit
repository the same pages are in `docs/user/`, and every evalkit server shows
them under `/docs`. In the docs, `{{origin}}` means your evalkit server's URL.

Just trying evalkit out? Start with the try-it stack in [`try/`](try/),
following [Try locally](../evalkit-docs/try-locally.md): one `docker compose
up` runs the server on this machine with your LLM as the judge, and a sample
agent shows the default path (3a, an SDK run with traces) end to end. Come
back here for a real deployment.

## Copy this checklist and track progress

```
Deployment:
- [ ] 1. Deploy the server (docker compose up)
- [ ] 2. evalkit db upgrade + admin bootstrap + evalkit login + create teammate users
- [ ] 2b. Create the shared workspaces (dev, prod) and add writers and reviewers
- [ ] 3. Choose the integration path with the client: 3a SDK + traces (default), 3b Harness API for everything, 3c Harness API for invocation + traces for trajectories
- [ ] 3a. (default) no harness: the client runs experiments with `run_experiment`; traces give the trajectories
- [ ] 3b. Implement the full Harness API in the client's harness (references/harness-api.md), trajectory endpoint included, and `evalkit agents create` it in each shared workspace
- [ ] 3c. Implement the Harness API without its trajectory endpoint; create the agent with `--trajectory-source otel --mapping`, mint an agent token, point the exporter at the collector
- [ ] 4. Implement Custom Evaluator API endpoints, if the client wants whitebox checks, and `evalkit evaluators create` each one
- [ ] 5. Write config.yaml (references/config-and-deploy.md), including `credentials:`, and restart the server
- [ ] 6. evalkit sync a suite of your cases
- [ ] 7. Wire `evalkit lint` / `evalkit sync` into the client's CI
- [ ] 8. (3b/3c) evalkit harness check <name>@dev — all declared capabilities pass
- [ ] 9. first experiment completes: (3a) `run_experiment(..., include_drafts=True)` from the client's script, (3b/3c) evalkit run finance@dev --agent <name>@dev --include-drafts
- [ ] 10. Hand off: server URL, usernames, and the evalkit-usage skill to the team
```

## 1–2. Server up, authenticated

Deployment shape, config keys, and the compose file are in
[`references/config-and-deploy.md`](references/config-and-deploy.md). The
server needs: Postgres (it holds everything: users, workspaces, agents,
cases, experiments, scores — bundled in compose or external), a blob URL,
and an OpenAI-compatible judge endpoint. The first start needs
`EVALKIT_ADMIN_PASSWORD`: it creates the `admin` user. It must be at least 8
characters and at most 72 bytes. Without it, a server with no admin refuses
to start and says so. Then `evalkit login` as admin
and `evalkit users add <name>` for each teammate (admin only). Each new user
gets a personal workspace named after them.
Which outbound calls the server makes, and how to restrict them, is under
"Outbound URLs and network egress" in the same reference.

## 2b. Workspaces

A workspace holds datasets, agents, remote evaluators and experiments.
There are two types:

- **Personal:** one per user, named after them. Only the owner writes to it.
  Nobody reviews cases there.
- **Shared:** created by an admin. Admins and its writers write to it. Its
  reviewers approve cases, and `evalkit run` on its datasets runs only
  Approved cases unless `--include-drafts`.

Every user can read every workspace, including other users' personal
workspaces: datasets, cases, experiments, runs, trajectories, spans and
fixtures. Only the owner writes to a personal workspace. So traces a user
imports or exports into their personal workspace, such as production traces
pulled for case mining, can be read by everyone on the server. This is by
design. References are `name@workspace`; a
bare `name` in the CLI means your personal workspace.

Recommended setup: one shared workspace per deployed environment of the
client's agent, usually `dev` and `prod`. Cases move personal → `@dev` →
`@prod`: a developer drafts in their own workspace, CI syncs merged cases
into `@dev` where reviewers approve them, and `@prod` holds the released set.

```bash
evalkit workspaces create dev                           # admin only
evalkit workspaces create prod
evalkit workspaces members add dev alice --role writer  # may sync and run in @dev
evalkit workspaces members add dev bob --role reviewer  # may approve cases in @dev
evalkit workspaces members list dev
```

Who drives a run depends on the agent, not on a flag: your code drives a
code-driven agent (an SDK run), the CLI drives a personal Harness API agent,
and the server drives a shared one. The full table, with examples for `dev`
and `prod`, is on the [Workspaces](../evalkit-docs/workspaces.md) page.

An experiment lives in the dataset's workspace only when both the dataset
and the agent are shared; otherwise it lives in the runner's personal
workspace.

## 3. Choose the integration path

Make this an explicit decision with the client before writing any
integration code; record the choice in the checklist. Two things are being
decided: who invokes the agent, and where trajectories come from.

| Path | Invocation, status, results | Trajectories | Pick when |
| -- | -- | -- | -- |
| **3a. SDK + traces** (default) | the client's own code, through `run_experiment` | OpenTelemetry spans exported to the collector | always, unless one of the two below applies |
| **3b. Harness API for everything** | evalkit's server, through the Harness API | the harness's own `GET /runs/{id}/trajectory` (ATIF) | the client's developers are willing to write and host the ATIF converter, or the agent's record holds more than its spans (the Claude Agent SDK is the usual example) |
| **3c. Harness API + traces** | evalkit's server, through the Harness API minus the trajectory endpoint | OpenTelemetry spans exported to the collector | runs must be started by the server (UI, schedules) rather than by client code |

Traces work for any instrumentation. Every OTel agent needs a span
mapping: the **connect-traces** skill ships starting points for
OpenInference and GenAI semantic conventions and writes one for anything
else. Pass it with `--mapping` when you create the agent.

## 3a. SDK + traces

No harness to deploy. The client installs the SDK into the agent's project,
creates a code-driven agent (`evalkit agents create <name>
--trajectory-source otel --mapping mapping.json`, no `--harness-url`),
registers the exporter and runs experiments with `run_experiment`; local runs
use the developer's own login for the API and the collector. That whole path is the
**evalkit-usage** skill (its "SDK runs" section) and the **connect-traces**
skill for the mapping. Your part as deployer: the server is up (steps 1–2)
and, when the agent also runs as a deployed service that should export
traces outside a developer's session, an agent token (3c below).

## 3b. Harness API for everything

The client's agent harness must expose the endpoints in
[`references/harness-api.md`](references/harness-api.md): capabilities, run
start/poll/stop, an ATIF trajectory endpoint, and optionally run listing and
workspace files. The generated OpenAPI schemas ship inside the evalkit CLI —
run `evalkit contracts export --target <dir>` to write `harness-api-v1.json`
and `atif-accepted.json` locally, and point the client's implementation at
those, not at prose. For the trajectory itself the ATIF RFC (pinned in the
reference) is the source of truth; the reference lists the RFC rules evalkit's
metrics depend on and the table of evalkit's `extra` conventions, which is the
only place they are defined.

An admin registers the deployed harness once per shared workspace it serves
(only admins may set a shared agent's `--harness-url`), and puts its API key in `credentials.agents` keyed by the same reference:

```bash
evalkit agents create ouragent --workspace dev --harness-url https://agent-dev.internal/evalkit/v1
evalkit agents create ouragent --workspace prod --harness-url https://agent.internal/evalkit/v1
```

Run settings (`--max-concurrent-runs`, `--run-timeout`, `--poll-interval`,
`--stop-grace`, `--trajectory-wait`) are flags on `agents create` and
`agents edit`; see `evalkit agents create --help`. Two more rules:

- **Capabilities honesty.** Declare only what actually works; evalkit
  neutral-skips evaluators whose inputs a harness doesn't provide, which is
  correct behaviour, whereas a false `true` produces failing scores blamed on
  the agent.
- **Implement stop.** evalkit calls `POST /runs/{id}/stop` on timeout when
  `accepts.stop` is true. A harness without stop leaks its own resources every
  time a run times out. After a stop, report a terminal status once
  `GET /runs/{id}/trajectory` returns the run's full trajectory: evalkit
  polls for up to `stop_grace_seconds`, then fetches the trajectory once and
  stores what it gets.

## 3c. Harness API + traces

Implement the Harness API as in 3b but without the trajectory endpoint.
Create the agent with `--trajectory-source otel --mapping mapping.json`
(the mapping's `answer` and `exclude_spans` say where the answer comes from
and which spans to leave out), have the harness declare
`provides.trajectory: false`, put the run id it returns on its spans in the
attribute your mapping's `session_id` rule names (`session.id` in the
OpenInference mapping, `gen_ai.conversation.id` in the GenAI one), and point its OTLP exporter at
`<server>/api/otel/v1/traces` with an agent token:

```bash
evalkit tokens create --agent <name>@dev   # admin or a writer of @dev; shown once
```

Set the token as `EVALKIT_OTEL_TOKEN` (and `EVALKIT_SERVER_URL`) on the
deployed agent; the evalkit SDK's `tracing.register` picks both up, and any
other exporter takes it in `OTEL_EXPORTER_OTLP_HEADERS`.

A deployed agent usually already exports to a tracing backend (SigNoz,
Datadog, Honeycomb). The standard OTLP environment variables name one
endpoint, so sending the same spans to evalkit too needs one of:

- **An OpenTelemetry Collector that fans out (recommended in production).**
  The agent keeps exporting to the Collector it already uses; add a second
  `otlphttp` exporter to its traces pipeline:

  ```yaml
  exporters:
    otlphttp/evalkit:
      traces_endpoint: https://<server>/api/otel/v1/traces
      headers:
        Authorization: Bearer ${env:EVALKIT_OTEL_TOKEN}
        X-Evalkit-Agent: <name>@dev
  service:
    pipelines:
      traces:
        exporters: [otlp/existing, otlphttp/evalkit]
  ```

  No agent code changes, and an evalkit outage never blocks the agent.
- **A second exporter in code.** In Python, `tracing.register` already does
  this: it adds evalkit's exporter to the existing provider. In other
  languages add a second batch span processor with an OTLP/HTTP exporter
  pointed at the collector.

Two collector facts that matter for proxies and Collectors in between:

- The collector speaks **OTLP over HTTP only** (protobuf or JSON, gzip or
  not). An exporter configured for gRPC gets nothing through; use
  `otlphttp`, not `otlp`, in a Collector.
- The JavaScript exporter sends chunked bodies with no `Content-Length`.
  evalkit accepts them; a proxy in front of it must not require
  `Content-Length`.

The server-side
knobs (`otel.settle_seconds`, `otel.max_batch_bytes`, `otel.max_session_bytes`)
and how the size limits relate to the server's memory limit are in
[`references/config-and-deploy.md`](references/config-and-deploy.md). The
**connect-traces** skill writes the span mapping; later versions are drafted
and activated in the UI.

## 4. Custom Evaluator API (optional, whitebox)

Any check that knows the client's internals — required tool calls, trajectory
shape against real tool names — lives on the client's side behind the Custom
Evaluator API (`custom-evaluator-api-v1.json` from `evalkit contracts
export`): one synchronous POST
endpoint per named evaluator, receiving the case config verbatim plus the run's
answer and ATIF trajectory plus its structured `output` (parsed, or null),
returning one or more named scores (`score: null` = no reading). When the
harness sent only `output`, `run.answer` is that output as canonical JSON;
read `run.output` rather than re-parsing it. Errors and timeouts are recorded, never fatal. Register each
endpoint in the workspace of the agents it scores, and put its API key in
`credentials.evaluators`:

```bash
evalkit evaluators create required_tool_calls --workspace dev --endpoint https://evals.internal/required --kind score
```

A run is scored with the remote evaluators in its agent's workspace.

## 5–6. Config and your first suite

Copy this skill's [`config.example.yaml`](config.example.yaml) to `config.yaml`,
fill it per [`references/config-and-deploy.md`](references/config-and-deploy.md),
and restart. Then sync a suite of your cases:

1. If the cases use fixtures, set `fixtures:` in the developer's
   `evalkit.yaml`. `evalkit sync` uploads any fixture bytes the server
   doesn't hold yet.
2. `evalkit sync <cases-dir>/<suite> --workspace dev` (an admin or a writer
   of `@dev`).

Repeat step 2 whenever the cases change.

If an admin has linked workspaces with `evalkit workspaces promote-to <source>
<target>`, sync only into the source: the target is fed by approvals and
refuses `evalkit sync`. Create each target dataset once with `evalkit datasets
create <name>@<target>` before the first approval.

## 7. CI wiring

The client owns sync cadence. Recommended: a CI job on their eval-cases
directory that runs `evalkit lint <dir> --dataset <their-slug>` on every PR
(fail on errors) and `evalkit sync <dir> --workspace dev` on merge to
their default branch (the dataset name is the directory name). Both commands
are thin HTTP calls. Each job installs the CLI from the server first with the
CI install command in the evalkit-usage skill, so CI always runs the server's
build. Both jobs log in first: every server call needs a token, lint included
(only `evalkit lint --offline` runs without one, and it skips the checks that
need server state). Use admin for both. Each job runs `evalkit login
--url <server> --username admin --password
"$EVALKIT_ADMIN_PASSWORD"` with the admin password from CI secrets (the same
value the server was started with) — tokens expire after
`auth.token_ttl_hours`, so do not store a token as a secret. Pass
`--actor <merge author>` on sync (for example the head commit's author email)
so the case changelog records who synced instead of `admin`. `--dataset` on
lint is what makes the server check case ids and overlay targets against
every other dataset; without it those two checks only run at sync.

## 8–9. Verify live

On the Harness API paths (3b, 3c), `evalkit harness check <name>@dev` runs a trivial agent call (with one
input file attached by url when `accepts.file_urls`, or uploaded when only
`accepts.files`) and exercises every declared
capability, printing a per-capability pass/fail table — fix fails before
moving on. Then run the first real experiment:
`evalkit run finance@dev --agent <name>@dev --include-drafts` (start with
`--case-id` for one case if agent runs are expensive). On the SDK path (3a)
the first experiment is
`run_experiment(dataset="finance@dev", include_drafts=True, ...)` from the
client's script, with `dry_run=True` first; `evalkit traces check` on the
printed session id is the equivalent of the harness check.

A freshly synced shared dataset has no approved cases yet: every case starts
as a draft, and without `--include-drafts` (`include_drafts=True`) the run
stops with "no approved cases in finance@dev match the selection". Drop the
flag once reviewers have approved the cases (evalkit-usage, "Case review").

## 10. Hand off

Give the team the server URL, their usernames, and the evalkit-usage skill
(`evalkit skills install` vendors it, along with authoring-eval-cases and
eval-driven-development, into a repo's agent-skills directory). They
install the CLI from `<server url>/cli`. Upgrades of the deployment itself
follow the "Upgrades" section of
[`references/config-and-deploy.md`](references/config-and-deploy.md); after
one, every CLI prints an update nudge until its owner runs `evalkit update`.

For bugs, feature requests and feedback on evalkit, open an issue at https://github.com/inferedge-ai/evalkit/issues; questions go to https://github.com/inferedge-ai/evalkit/discussions.
