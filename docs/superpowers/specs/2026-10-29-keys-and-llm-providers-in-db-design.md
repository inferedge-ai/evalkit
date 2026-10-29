# API keys and LLM providers in the database

Status: draft, 2026-10-29.

## Problem

A shared agent's `harness_url` is a database row, but the API key the server
sends to that harness is not. Neither is the default judge or the judge's key.
They all live in the server config:

```yaml
judge_default:
  base_url: https://api.openai.com/v1
  model: gpt-5
credentials:
  agents: {ouragent@dev: env:OURAGENT_DEV_KEY}
  evaluators: {compliance@dev: env:COMPLIANCE_KEY}
  judges: {https://api.openai.com/v1: env:OPENAI_API_KEY}
```

So adding a shared agent or remote evaluator takes two people and a restart:
a writer or admin creates the row, then the operator adds a `credentials:`
entry and an env var and restarts the server. Until the restart, server runs on
the new agent fail with `400 no credentials.agents entry for <ref> in the
server config`. Changing the judge works the same way.

Goals:

- A writer on a shared workspace can add or edit a shared agent or remote
  evaluator, including its URL and key, with no config change and no restart.
- An admin can add an LLM provider (base URL and key) and set the default judge
  without a restart. Writers can still pick a different judge per experiment.

Not a goal: tenants. A hosted, multi-tenant evalkit needs its own design. Moving
these settings into DB rows is a step toward it, since rows can later carry a
tenant id and a config file cannot.

## Terms

| Term | Meaning |
| -- | -- |
| Stored key | An API key saved in the DB, encrypted with the server's `encryption_key`. |
| LLM provider | An OpenAI-compatible endpoint evalkit may call: a name (`openai`), a base URL and a stored key. Today only judges use providers; other features may later. |
| Default judge | The judge (provider, model, effort, limits) an experiment uses when it does not set its own. Set by an admin. |
| Endpoint | Any URL the server calls on a user's behalf: a harness URL, a remote evaluator endpoint, or an LLM provider base URL. |

## Why it is this way today

The retire-Phoenix data model design (evalkit-private,
`docs/superpowers/specs/2026-10-13-retire-phoenix-data-models-design.md`)
decided:

- **D4:** no API keys and no env var names in the DB, except trace source keys,
  which are stored encrypted. Reason: "Secrets stay in env."
- **D6:** the judge is a per-experiment setting, with a non-secret server
  default (`judge_default` in config).
- **The driver owns the credentials.** The `credentials:` map has the same shape
  in server config and in a developer's `evalkit.yaml`. A personal agent is
  driven by its owner's CLI, so its key never reaches the server.
- **Keys are looked up by reference** (`name@workspace`), not stored with the
  URL. So only admins may set a `harness_url` or an evaluator endpoint
  (`can_set_endpoint` in `src/evalkit/core/access.py`): otherwise a writer could
  point `ouragent@dev` at their own server and receive the operator's key.

Config had real benefits: setup lives in git and deploy values, a fresh server
comes up fully configured, and keys stay in the operator's secret store. This
design gives those up on the server for self-service and for a later hosted
service. The CLI side (`evalkit.yaml` + `.env`) keeps them.

## Decisions

| # | Decision | Why |
| -- | -- | -- |
| K1 | Shared agents and shared remote evaluators store their API key in the DB, encrypted with the server's `encryption_key` (Fernet, as trace sources already do). | A writer can add one, URL and key together, in one step: no operator, no config change, no restart. |
| K2 | LLM providers are DB rows with a stored key. The default judge is a DB setting that references a provider. Admins manage both. | An admin can add a provider or change the judge without a restart. A hosted evalkit cannot restart per customer. |
| K3 | The DB is the only source on the server. Server config may no longer hold `credentials:` or `judge_default`; if it does, the server refuses to start and prints the commands that set the same values in the DB. | One source of truth; no guessing which key is live. |
| K4 | Personal agents and evaluators keep their keys in `evalkit.yaml` / `.env`. The API refuses a key on a personal agent or evaluator. | The CLI drives them; their keys stay on the laptop. |
| K5 | Writers on a shared workspace may set and change an agent's or evaluator's URL and key (`can_set_endpoint` becomes `can_write`). | Adding an agent should not need an admin. |
| K6 | Changing a URL clears the stored key, unless the same request sets a new one. Applies to agents, evaluators and LLM providers. | Otherwise writer A could redirect a key that writer B entered. |
| K7 | Keys are write-only. Reads return `has_api_key: bool`, never the value. | Standard practice for secrets: a key that is never returned cannot leak through the API, the UI, logs or a screenshot. Trace source keys already work this way. |
| K8 | The server refuses endpoints that resolve to loopback, private, link-local or unspecified addresses, unless server config sets `allow_private_endpoints: true`. | Writers can now set URLs the server calls, and the server stores the response where users can read it (SSRF). |
| K9 | A server call to an endpoint with no stored key goes ahead without the key header, and the experiment (and its create response) carries a warning. | Endpoints inside the server's network may have no auth, e.g. an in-cluster harness or a local LLM. The warning makes a forgotten key easy to spot when a 401 follows. |
| K10 | A local run with no `evalkit.yaml` entry for its agent or an evaluator prints one warning line and calls without the key header. | Local endpoints often have no auth; the person who forgot the key is watching the terminal. |
| K11 | A server-driven experiment may only use a judge whose base URL is a registered LLM provider. | The judge prompt holds cases and answers; a writer must not be able to send them to an arbitrary URL with the server's help. |
| K12 | `evalkit.yaml` renames `credentials.judges` to `credentials.llm_providers`. The old name fails to load with a message naming the new one. | Same word in the CLI, the server and the docs. |
| K13 | Every UI form and CLI command that takes an endpoint shows a short note: the server at that URL receives the key entered here and the data evalkit sends it. Text only. | Users should know what they are trusting when they paste a URL. |

## Data model

One alembic migration (the operator runs it):

```text
agents             + api_key_sealed bytea NULL
remote_evaluators  + api_key_sealed bytea NULL

llm_providers                      (new; AuditMixin)
  id              int PK
  name            text UNIQUE      -- "openai"; a label for the CLI and UI
  base_url        text UNIQUE      -- how judges and evalkit.yaml refer to it
  api_key_sealed  bytea NULL
  archived_at     timestamptz NULL

server_settings                    (new; one row)
  id              int PK CHECK (id = 1)
  default_judge   jsonb NULL       -- JudgeSettings
```

- `SecretBox` (`src/evalkit/db/crypto.py`) seals and opens keys, as
  `TraceSourceStore` does today. Personal agent and evaluator rows always hold
  `NULL`.
- `JudgeSettings` (`src/evalkit/contracts/settings.py`) is unchanged: it keeps
  `base_url`. Experiments already snapshot it, and local runs key
  `credentials.llm_providers` by base URL, so the provider's base URL is the
  join key. `name` is only for people.
- `default_judge.base_url` must match an active provider. Archiving that
  provider is refused until the default judge moves.

## Access rules

```python
def can_set_endpoint(actor, ws):          # harness_url, evaluator endpoint, api key
    return can_write(actor, ws)           # was: shared → actor.is_admin

def can_manage_llm_providers(actor):      # providers and the default judge
    return actor.is_admin
```

Writers pick any registered provider and any model for their experiment's
judge (K11); they do not add providers.

## Setting and reading keys

API (`src/evalkit/api/agents.py`, `src/evalkit/api/evaluators.py`, new
`src/evalkit/api/llm_providers.py`):

- `POST` and `PATCH` bodies gain `api_key: str | None`. `PATCH` with
  `"api_key": null` clears it.
- Views gain `has_api_key: bool`.

```python
def save(row, body, ws):
    if "api_key" in body.fields_set and ws is personal:
        400 "personal agents and evaluators read their key from evalkit.yaml"
    if body.api_key is not None and box is None:
        400 "encryption_key is not configured; set encryption_key: env:EVALKIT_ENCRYPTION_KEY"

    if new_url(body) != row.url:
        check_endpoint(new_url(body), cfg.allow_private_endpoints)   # K8
        row.api_key_sealed = None                                    # K6

    if "api_key" in body.fields_set:
        row.api_key_sealed = box.seal(body.api_key) if body.api_key else None
```

## LLM providers and the default judge

API:

| Method | Path | Who |
| -- | -- | -- |
| `GET` | `/api/llm-providers` | everyone (name, base URL, `has_api_key`) |
| `POST` | `/api/llm-providers` | admin |
| `PATCH` | `/api/llm-providers/{name}` | admin |
| `POST` | `/api/llm-providers/{name}/archive`, `/restore` | admin |
| `GET` | `/api/judge-default` | everyone (already exists; now reads the DB) |
| `PUT` | `/api/judge-default` | admin |

`GET /api/judge-default` returns 404 `no default judge; an admin sets one with
evalkit judge-default set` until one is set.

Experiment create (`api/experiments.py`):

```python
judge = body.judge or settings.default_judge
if judge is None:
    400 "no default judge; pass --judge-base-url and --judge-model, or ask an admin to set one"
if server_driven:
    provider = llm_providers.active_by_base_url(judge.base_url)
    if provider is None:                                              # K11
        400 f"{judge.base_url} is not a registered LLM provider; "
            f"registered: {', '.join(p.base_url for p in active)}"
```

Local runs keep their judge flow: the CLI or SDK reads `/api/judge-default`
when no judge is given, and takes the key from `evalkit.yaml`
`credentials.llm_providers[base_url]`. K11 does not apply: the developer's own
process makes the call with their own key.

## CLI

Agents and evaluators:

- `evalkit agents create|edit <ref> --api-key-env NAME` reads the key from the
  env var `NAME` on the laptop and sends it. Taking the name, not the value,
  keeps the key out of shell history.
- `--clear-api-key` sends `"api_key": null`.
- The same two flags on `evalkit evaluators create|edit`.
- `evaluators show` and `evaluators restore` are added, to match agents.
- `agents show` / `evaluators show` print `api key: set` or `api key: not set`.

LLM providers and the default judge (admin):

```text
evalkit llm-providers create openai --base-url https://api.openai.com/v1 --api-key-env OPENAI_API_KEY
evalkit llm-providers edit openai [--base-url URL] [--api-key-env NAME | --clear-api-key]
evalkit llm-providers list | show <name> | archive <name> | restore <name>
evalkit judge-default show
evalkit judge-default set --provider openai --model gpt-5 [--effort high] [--max-tokens N] [--concurrency N]
```

`judge-default set --provider` resolves the name to its base URL.

`evalkit.yaml`:

```yaml
credentials:
  agents: {myagent: env:MYAGENT_KEY}
  evaluators: {compliance@dev: env:COMPLIANCE_KEY}
  llm_providers: {https://api.openai.com/v1: env:OPENAI_API_KEY}   # was: judges
```

## UI

- **Agents** (exists): the form gains a password field for the key. It shows
  "Key set" with Replace and Clear buttons when `has_api_key` is true. When the
  URL changes, the form says saving clears the key and asks for a new one. The
  "New agent" and "Edit" buttons already show only for writers
  (`canWrite`, `ui/src/agents/AgentsPage.tsx`); with K5 a writer's
  `harness_url` is no longer refused.
- **Evaluators** (new page per workspace, beside Agents): list, create, edit,
  archive, restore, same key field. Create and edit show only when `canWrite`.
- **LLM providers** (new admin page, beside Users and Trace sources): list,
  create, edit, archive, restore, same key field, and a "Default judge" form.
  Shown only to admins.
- **Experiment start** (where the UI starts server runs): the judge picker
  lists registered providers, then a model field; it defaults to the default
  judge.

## Endpoint note (K13)

Every UI form field and CLI `--help` text that takes an endpoint shows the same
note, adjusted for the noun:

> Check this URL before saving. The server at this address receives the API key
> you enter here and the data evalkit sends it: cases, answers and trajectories.

Places: agent `harness_url`, evaluator endpoint, LLM provider base URL, and the
experiment judge base URL (UI picker and `--judge-base-url`). Trace source URLs
get it too. No extra checks beyond K8.

## Looking keys up when the server drives

`RunnerDeps.credentials` is a `CredentialRegistry` today, and the runner calls
the sync `key_for(kind, ref)` and `require_judge_key(base_url)` from inside a
run (`src/evalkit/core/runner.py`). To keep the runner unchanged, the server
loads the keys it needs before it starts work and hands the runner an object
with the same interface.

```python
class KeySource(Protocol):                       # src/evalkit/credentials.py
    def key_for(kind, ref) -> str | None         # kind: agent | evaluator | llm_provider
    def require_judge_key(base_url) -> str | None

# CLI / SDK: CredentialRegistry (evalkit.yaml + .env).

class StoredKeys:                                 # server only, src/evalkit/db/
    keys: dict[(kind, ref), str]                  # opened from the DB

    def key_for(kind, ref):
        return keys.get((kind, ref))

async def stored_keys(agent_ref, evaluator_refs, judge_base_url) -> StoredKeys:
    rows = agents[agent_ref] ∪ remote_evaluators[evaluator_refs]
           ∪ llm_providers[base_url = judge_base_url]
    return StoredKeys({(kind, r.ref): box.open(r.api_key_sealed)
                       for r in rows if r.api_key_sealed is not None})
```

`require_judge_key` returns `None` when the provider has no key (K9); the judge
client then sends no `Authorization` header.

Every server call site that uses `deps.credentials` today builds a
`StoredKeys` for what it touches:

- experiment create and resume (`api/experiments.py`), which also add the K9
  warnings:

  ```python
  missing = [ref for (kind, ref) in needed if keys.key_for(kind, ref) is None]
  for ref in missing:
      warn f"{ref} has no API key; calling it without one"
  ```

- re-evaluate, the harness check (`api/harness_check.py`), imports
  (`api/imports.py`) and agent runs (`api/runs.py`).

A running experiment keeps the keys it loaded. A key change applies on the
next create, resume or re-evaluate.

## Endpoint check (K8)

```python
BLOCKED = loopback ∪ private (RFC 1918, fc00::/7) ∪ link-local (incl. 169.254.169.254)
          ∪ unspecified ∪ multicast

def check_endpoint(url, allow_private):
    if allow_private: return
    addrs = resolve(host(url))                    # every A / AAAA record
    if addrs fail to resolve:
        400 f"{url}: cannot resolve {host}"
    if any(a ∈ BLOCKED for a in addrs):
        400 f"{url} resolves to a private address; the operator can allow it "
            f"with allow_private_endpoints: true"
```

- Runs on save when the URL changes, and again on experiment create, resume,
  re-evaluate and harness check, because DNS can change between save and use.
- It does not run on every HTTP request in a run. A DNS change during a run
  (rebinding) is not covered; operators who need that run the server in a
  network without access to internal services.
- Server config: `allow_private_endpoints: bool = False`. Deployments whose
  harness, evaluator or LLM runs in the same cluster or on
  `host.docker.internal` set it to `true`.

## Local runs (K10)

Before the first case, for the agent and each remote evaluator without an
`evalkit.yaml` entry:

```text
no credentials.agents entry for myagent in evalkit.yaml; calling the harness without x-api-key
```

The judge keeps today's rule: a missing `credentials.llm_providers` entry fails
before the first case, since hosted LLM APIs always need a key.

## Startup and rekey

- Server `Config` validation fails when `credentials` or `judge_default` is
  present, and prints the commands that recreate them in the DB:

  ```
  credentials and judge_default moved to the database. Remove them from the
  config, start the server, then run (as an admin, with the keys in your env):
    evalkit llm-providers create <name> --base-url https://api.openai.com/v1 --api-key-env OPENAI_API_KEY
    evalkit judge-default set --provider <name> --model gpt-5
    evalkit agents edit ouragent@dev --api-key-env OURAGENT_DEV_KEY
    evalkit evaluators edit compliance@dev --api-key-env COMPLIANCE_KEY
  ```

- `evalkit.yaml` with `credentials.judges` fails to load: `credentials.judges
  is now credentials.llm_providers`.
- Startup refuses to run when any row has a stored key and `encryption_key` is
  not set (as it does today for trace sources).
- `evalkit trace-sources rekey` is replaced by `evalkit db rekey`, which
  re-encrypts trace sources, agents, remote evaluators and LLM providers in one
  transaction.

## Upgrade steps for an operator

1. Run the migration.
2. Remove `credentials` and `judge_default` from the server config. Make sure
   `encryption_key` is set. If a harness, evaluator or LLM is on a private
   address, set `allow_private_endpoints: true`.
3. Start the server.
4. Re-enter each value the old config held, using the commands from the
   startup error. Whoever runs them needs each key in their own shell env,
   copied from wherever the operator keeps it (the deployment's secret store).
   An admin does the LLM providers and the default judge; an admin or a writer
   of that workspace does each agent and evaluator. Until a key is set, runs
   call that endpoint without one and warn (K9); until the default judge is
   set, experiments must pass a judge.
5. Developers rename `credentials.judges` to `credentials.llm_providers` in
   their `evalkit.yaml`.

## Try-it stack

`skills/evalkit-deployment/try/config.yaml` sets `judge_default` and
`credentials.judges` from `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` today,
so the stack judges with no clicks. Those keys go away. The compose file gains
a one-shot `setup` service that waits for the server, logs in as `admin` with
`EVALKIT_ADMIN_PASSWORD`, and runs `llm-providers create` and `judge-default
set` from the same three env vars. The stack sets `allow_private_endpoints:
true` so a local LLM works. `try/evalkit.yaml` renames its map per K12.

## Docs and changelog

- `docs/user/config.md`, `docs/user/try-locally.md`, `docs/user/quickstart.md`,
  `docs/user/sdk-runs.md`, `skills/evalkit-deployment/` (config example,
  `references/config-and-deploy.md`, `try/`), `skills/evalkit-usage/SKILL.md`,
  and `docs/flows/01`, `06`, `07`, `08`, `15`, `16`, `17`.
- AGENTS.md terms: "Credential registry" now means the CLI side only; add
  "LLM provider", "Default judge", "Stored key".
- `CHANGELOG.md` `## Unreleased`: the new commands, flags, fields and pages;
  the removed server config keys and the startup error; the `evalkit.yaml`
  rename; `db rekey`; writers can now set URLs; K11.

## Testing

- `core/access.py`: writers may set endpoints on shared workspaces; reviewers
  and non-members may not; only admins manage providers.
- API, for agents, evaluators and providers: create with key → view has
  `has_api_key: true` and no key value; URL change clears the key; URL change
  plus new key keeps the new key; key on a personal agent → 400; key without
  `encryption_key` → 400.
- `check_endpoint`: blocked and allowed addresses, IPv6, a host with mixed
  records, `allow_private_endpoints` bypass.
- Experiment create: no stored agent key → starts with a warning; stored key
  reaches the fake harness as `x-api-key`; unregistered judge base URL on a
  server run → 400 (K11); no judge and no default → 400; archiving the
  default judge's provider → 409.
- Config with `credentials` or `judge_default` fails to load and lists the
  commands. `evalkit.yaml` with `credentials.judges` fails with the rename
  message.
- `db rekey` re-encrypts all four tables.
- Local run without an agent entry prints the warning and sends no header.
- UI: Evaluators page hides create and edit for non-writers; LLM providers page
  is admin-only; the endpoint note renders on every endpoint field.
- e2e: the setup creates the fake judge as a provider, sets the default judge,
  and sets the fake harness key with `--api-key-env`, with
  `allow_private_endpoints: true`.

## Not in scope

- Tenants.
- Storing keys for personal agents on the server.
- A per-agent "no auth" setting. K9's warning covers keyless endpoints.
- Using LLM providers for anything but judges.
