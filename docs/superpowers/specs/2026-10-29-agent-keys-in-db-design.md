# Agent and evaluator API keys in the database

Status: draft, 2026-10-29.

## Problem

A shared agent's `harness_url` is a database row, but the API key the server
sends to that harness is not. It lives in the server config:

```yaml
credentials:
  agents: {ouragent@dev: env:OURAGENT_DEV_KEY}
  evaluators: {compliance@dev: env:COMPLIANCE_KEY}
  judges: {https://api.openai.com/v1: env:OPENAI_API_KEY}
```

So adding a shared agent or remote evaluator takes two steps by two people:
a writer or admin creates the row, and the operator adds a `credentials:` entry
and an env var, then restarts the server. Until the restart, server runs on the
new agent fail with `400 no credentials.agents entry for <ref> in the server
config`.

Goal: a writer on a shared workspace can add or edit a shared agent or remote
evaluator, including its URL and key, with no config change and no restart.

## Why it is this way today

The retire-Phoenix data model design (evalkit-private,
`docs/superpowers/specs/2026-10-13-retire-phoenix-data-models-design.md`)
decided:

- **D4:** no API keys and no env var names in the DB, except trace source keys,
  which are stored encrypted. Reason: "Secrets stay in env."
- **The driver owns the credentials.** The `credentials:` map has the same shape
  in server config and in a developer's `evalkit.yaml`. A personal agent is
  driven by its owner's CLI, so its key never reaches the server.
- **Keys are looked up by reference** (`name@workspace`), not stored with the
  URL. So only admins may set a `harness_url` or an evaluator endpoint
  (`can_set_endpoint` in `src/evalkit/core/access.py`): otherwise a writer could
  point `ouragent@dev` at their own server and receive the operator's key.

This design keeps the CLI side of that model and changes the server side.

## Decisions

| # | Decision | Why |
| -- | -- | -- |
| K1 | Shared agents and shared remote evaluators store their API key in the DB, encrypted with the server's `encryption_key` (Fernet, as trace sources already do). | Adding one needs no restart. |
| K2 | Judge keys stay in server config `credentials.judges`. | They are per base URL and server-wide; they change rarely. |
| K3 | The DB is the only source for shared agent and evaluator keys. Server config may no longer hold `credentials.agents` or `credentials.evaluators`; if it does, the server refuses to start and lists the entries to move. | One source of truth; no guessing which key is live. |
| K4 | Personal agents and evaluators keep their keys in `evalkit.yaml` / `.env`, unchanged. The API refuses a key on a personal agent or evaluator. | The CLI drives them; their keys stay on the laptop. |
| K5 | Writers on a shared workspace may set and change the URL and the key (`can_set_endpoint` becomes `can_write`). | Adding an agent should not need an admin. |
| K6 | Changing the URL clears the stored key, unless the same request sets a new one. | Otherwise writer A could redirect a key that writer B entered. |
| K7 | Keys are write-only. Reads return `has_api_key: bool`, never the value. | Same as trace sources. |
| K8 | The server refuses endpoints that resolve to loopback, private, link-local or unspecified addresses, unless server config sets `allow_private_endpoints: true`. | Writers can now set URLs the server calls, and the server stores the response where users can read it (SSRF). |
| K9 | A server-driven experiment whose agent has no stored key is refused at create and resume (400). A server-driven call to a remote evaluator with no stored key fails that evaluator. | Fail before the run, not with a 401 on every case. |
| K10 | A local run whose agent has no `evalkit.yaml` entry prints one warning line and calls the harness without `x-api-key`. | Local harnesses often have no auth; the person who forgot the key is watching the terminal. |

## Data model

One alembic migration (the operator runs it):

| Table | New column | Type |
| -- | -- | -- |
| `agents` | `api_key_sealed` | `bytea`, nullable |
| `remote_evaluators` | `api_key_sealed` | `bytea`, nullable |

`SecretBox` (`src/evalkit/db/crypto.py`) seals and opens the values, as
`TraceSourceStore` does today. Personal rows always hold `NULL`.

## Access rules

```python
def can_set_endpoint(actor, ws):          # harness_url, evaluator endpoint, api key
    return can_write(actor, ws)           # was: shared → actor.is_admin
```

`can_set_endpoint` stays as a name so call sites read the same; it can be
inlined later.

## Setting and reading keys

API (`src/evalkit/api/agents.py`, `src/evalkit/api/evaluators.py`):

- `POST` and `PATCH` bodies gain `api_key: str | None`. `PATCH` with
  `"api_key": null` clears it.
- Views gain `has_api_key: bool`.

```python
def save(row, body, ws):
    if "api_key" in body.fields_set and not ws.is_shared:
        400 "personal agents read their key from evalkit.yaml"
    if "api_key" in body.fields_set and body.api_key is not None and box is None:
        400 "encryption_key is not configured; set encryption_key: env:EVALKIT_ENCRYPTION_KEY"

    url_changed = new_url(body) != row.url
    if url_changed:
        check_endpoint(new_url(body), cfg.allow_private_endpoints)   # K8
        row.api_key_sealed = None                                    # K6

    if "api_key" in body.fields_set:
        row.api_key_sealed = box.seal(body.api_key) if body.api_key else None
```

CLI:

- `evalkit agents create|update <ref> --api-key-env NAME` reads the key from
  the env var `NAME` on the laptop and sends it. Taking the name, not the value,
  keeps the key out of shell history.
- `--clear-api-key` sends `"api_key": null`.
- The same two flags on `evalkit evaluators create|update`.
- `agents show` / `evaluators show` print `api key: set` or `api key: not set`.

UI: the agent and evaluator settings forms gain a password field. It shows
"Key set" with Replace and Clear buttons when `has_api_key` is true. When the
URL field changes, the form warns that saving clears the key and asks for a new
one.

## Looking keys up when the server drives

`RunnerDeps.credentials` is a `CredentialRegistry` today, and the runner calls
the sync `key_for(kind, ref)` from inside a run (`src/evalkit/core/runner.py`).
To keep the runner unchanged, the server loads the keys it needs before it
starts work and hands the runner an object with the same interface.

```python
class KeySource(Protocol):                       # src/evalkit/credentials.py
    def key_for(kind, ref) -> str | None
    def require_judge_key(base_url) -> str

# CLI / SDK: CredentialRegistry, unchanged (evalkit.yaml + .env).

class StoredKeys:                                 # server only, src/evalkit/db/
    judges: CredentialRegistry                    # credentials.judges from server config
    keys: dict[(kind, ref), str]                  # opened from the DB

    def key_for(kind, ref):
        if kind == "judge": return judges.key_for(kind, ref)
        return keys.get((kind, ref))

async def stored_keys(agent_ref, evaluator_refs) -> StoredKeys:
    rows = agents[agent_ref] ∪ remote_evaluators[evaluator_refs]
    return StoredKeys(judges, {(kind, r.ref): box.open(r.api_key_sealed)
                               for r in rows if r.api_key_sealed is not None})
```

Every server call site that uses `deps.credentials` today builds a
`StoredKeys` for the agent and the evaluators it touches:

- experiment create and resume (`api/experiments.py`); these also apply K9:

  ```python
  if keys.key_for("agent", agent.ref) is None:
      400 f"agent {agent.ref} has no API key; set it with "
          f"evalkit agents update {agent.ref} --api-key-env NAME"
  ```

- the harness check (`api/harness_check.py`), imports (`api/imports.py`) and
  agent runs (`api/runs.py`).

A running experiment keeps the keys it loaded. A key change applies on the
next create or resume.

## Endpoint check (K8)

```python
BLOCKED = loopback ∪ private (RFC 1918, fc00::/7) ∪ link-local (incl. 169.254.169.254)
          ∪ unspecified ∪ multicast

def check_endpoint(url, allow_private):
    if allow_private: return
    addrs = resolve(host(url))                    # every A / AAAA record
    if any(a ∈ BLOCKED for a in addrs):
        400 f"{url} resolves to a private address; the operator can allow it "
            f"with allow_private_endpoints: true"
```

- Runs on save (when the URL changes) and again on experiment create, resume
  and harness check, because DNS can change between save and use.
- It does not run on every HTTP request in a run. A DNS change during a run
  (rebinding) is not covered; operators who need that run the server in a
  network without access to internal services.
- Server config: `allow_private_endpoints: bool = False`. Deployments whose
  harness runs in the same cluster or on `host.docker.internal` set it to
  `true`.

## Local runs (K10)

Before the first case, if `key_for("agent", ref)` is `None`:

```
no credentials.agents entry for myagent in evalkit.yaml; calling the harness without x-api-key
```

The same for each remote evaluator a local run calls without a key.

## Startup and rekey

- `Config` validation: if `credentials.agents` or `credentials.evaluators` is
  non-empty, loading fails with:

  ```
  credentials.agents and credentials.evaluators moved to the database.
  Remove these entries, start the server, then set each key:
    evalkit agents update ouragent@dev --api-key-env OURAGENT_DEV_KEY
    evalkit evaluators update compliance@dev --api-key-env COMPLIANCE_KEY
  ```

  `evalkit.yaml` keeps all three maps.
- Startup refuses to run when any agent or evaluator has a sealed key and
  `encryption_key` is not set (as it does today for trace sources).
- `evalkit trace-sources rekey` is replaced by `evalkit server rekey`, which
  re-encrypts trace sources, agents and remote evaluators in one transaction.

## Upgrade steps for an operator

1. Run the migration.
2. Remove `credentials.agents` and `credentials.evaluators` from server config.
   Keep `credentials.judges`. Make sure `encryption_key` is set.
3. If a harness or evaluator runs on a private address, set
   `allow_private_endpoints: true`.
4. Start the server.
5. For each shared agent and evaluator, run the `update --api-key-env` command
   printed in step 2's error (or listed in the changelog). Until then, server
   runs on that agent return the K9 400.

## Docs and changelog

- `docs/user/config.md`, `skills/evalkit-deployment/` (config example,
  `references/config-and-deploy.md`), `skills/evalkit-usage/SKILL.md`, and
  `docs/flows/06`, `15`, `16`, `17`: keys are set per agent; writers can set
  URLs; `allow_private_endpoints`.
- `CHANGELOG.md` `## Unreleased`: the new flags and fields, the removed config
  maps, the startup error, `server rekey`, and that writers can now set URLs.

## Testing

- `core/access.py`: writers may set endpoints on shared workspaces; reviewers
  and non-members may not.
- API: create with key → view has `has_api_key: true` and no key value; URL
  change clears the key; URL change plus new key keeps the new key; key on a
  personal agent → 400; key without `encryption_key` → 400.
- `check_endpoint`: blocked and allowed addresses, IPv6, a host with mixed
  records, `allow_private_endpoints` bypass.
- Experiment create and resume with no stored key → 400 with the command hint;
  with a stored key the fake harness receives it as `x-api-key`.
- Config with `credentials.agents` fails to load and lists the commands.
- `server rekey` re-encrypts all three tables.
- Local run without an entry prints the warning and sends no header.
- e2e: the fake harness on 18080 checks `x-api-key: client-key`; the e2e setup
  sets the key with `--api-key-env` and `allow_private_endpoints: true`.

## Not in scope

- Judge keys in the DB.
- Storing keys for personal agents on the server.
- A per-agent "no auth" setting for shared agents. Add `auth: none` if a
  keyless shared harness ever appears.
