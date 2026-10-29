# API keys and LLM providers in the database — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Shared agents, remote evaluators and LLM providers keep their API keys
in the DB; the default judge lives in the DB; writers can set endpoints; the
server config loses `credentials` and `judge_default`.

**Architecture:** New columns and tables behind the existing `SecretBox`
(Fernet). The runner keeps its sync `key_for` / `require_judge_key` interface
(`KeySource`); the server opens the keys an experiment needs from the DB before
it starts work and swaps them into a per-task copy of `RunnerDeps`. The CLI and
SDK keep `CredentialRegistry` over `evalkit.yaml`.

**Tech stack:** FastAPI, SQLAlchemy async, alembic, typer, React/TS (vitest).

**Spec:** `docs/superpowers/specs/2026-10-29-keys-and-llm-providers-in-db-design.md`

## Global constraints

- Column name for sealed keys: `api_key_encrypted`, to match
  `trace_sources.api_key_encrypted` (the spec says `api_key_sealed`; the
  existing name wins).
- No try/except unless the spec calls for it. No local imports. `X | None`.
  f-strings except stdlib logging. No new docstrings or comments.
- Layering: `core` never imports `db`, `api`, `client`, `sdk`. The endpoint
  check lives in `core/endpoints.py`.
- Migrations: write `0013`; never run it against anything but the local DB
  (`localhost:5432/evalkit`).
- Run tooling with `uv run`. UI: `cd ui && npm test`, `npx tsc -b`.
- Endpoint note text (K13), verbatim:
  "Check this URL before saving. The server at this address receives the API
  key you enter here and the data evalkit sends it: cases, answers and
  trajectories."

---

### Task 1: Schema and stores

**Files:**
- Create: `src/evalkit/db/alembic/versions/0013_keys_and_llm_providers.py`
- Modify: `src/evalkit/db/models.py` (Agent, RemoteEvaluator, new LlmProvider, ServerSettings)
- Create: `src/evalkit/db/llm_providers.py`
- Modify: `src/evalkit/db/agents.py`, `src/evalkit/db/evaluators.py` (set/clear key, `has_api_key`, open keys)
- Modify: `src/evalkit/core/agents.py`, `src/evalkit/core/remote_evaluators.py` (`has_api_key: bool = False`)
- Test: `tests/db/test_stored_keys.py`, `tests/db/test_llm_providers.py`

**Interfaces produced:**
- `AgentStore.set_api_key(ref, sealed: bytes | None)`, `AgentStore.sealed_key(ref) -> bytes | None`
- `RemoteEvaluatorStore.set_api_key(ref, sealed)`, `.sealed_keys(workspace) -> dict[str, bytes]`
- `AgentStore.edit(...)` clears the key when `harness_url` changes;
  `RemoteEvaluatorStore.edit` clears it when `settings.endpoint` changes.
- `LlmProviderStore(session_factory)`: `create(name, base_url, sealed)`,
  `edit(name, *, base_url=None, sealed=UNSET)`, `archive(name)`, `restore(name)`,
  `get(name)`, `list_providers(include_archived)`, `active_by_base_url(url)`,
  `sealed_key(base_url)`, `default_judge() -> JudgeSettings | None`,
  `set_default_judge(JudgeSettings)`. `LlmProviderView(name, base_url, has_api_key, archived)`.
- Errors: `UnknownLlmProvider(KeyError)`, `LlmProviderExists(ValueError)`,
  `ProviderInUse(ValueError)` (archiving the default judge's provider).
- `rekey_all(session_factory, old, new) -> int` in `db/crypto_rekey.py` covering
  trace sources, agents, evaluators, providers.

Steps: failing store tests (create/edit/clear on URL change/archive guard/
default judge round-trip/rekey) → migration + models → stores → pass → commit.

### Task 2: Endpoint check and access

**Files:**
- Create: `src/evalkit/core/endpoints.py`
- Modify: `src/evalkit/core/access.py` (`can_set_endpoint` → `can_write`)
- Modify: `src/evalkit/config.py` (`allow_private_endpoints: bool = False`)
- Test: `tests/core/test_endpoints.py`, `tests/core/test_access.py`

```python
class BlockedEndpoint(ValueError): ...

def blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (addr.is_loopback or addr.is_private or addr.is_link_local
            or addr.is_unspecified or addr.is_multicast or addr.is_reserved)

async def check_endpoint(url: str, *, allow_private: bool) -> None:
    if allow_private: return
    host = httpx.URL(url).host
    infos = await asyncio.get_running_loop().getaddrinfo(host, None)
    bad = sorted({i[4][0] for i in infos if blocked(ipaddress.ip_address(i[4][0]))})
    if bad: raise BlockedEndpoint(f"{url} resolves to a private address ({', '.join(bad)}); "
                                  "the operator can allow it with allow_private_endpoints: true")
```

Steps: tests for literal IPs (127.0.0.1, 10.x, 169.254.169.254, ::1, fc00::1,
8.8.8.8) and bypass → implement → commit.

### Task 3: Server config and key source

**Files:**
- Modify: `src/evalkit/config.py` (move `credentials` from `RunnerConfig` to
  `LocalConfig`; `Config` loses `judge_default`; a before-validator rejects
  `credentials` / `judge_default` with the K3 message)
- Modify: `src/evalkit/credentials.py` (`KeySource` protocol; kind
  `llm_provider`; `CredentialsConfig.judges` → `llm_providers`, `judges`
  rejected with the K12 message)
- Create: `src/evalkit/db/keys.py` (`StoredKeys`, `KeyLoader`, `NoKeysLoaded`)
- Modify: `src/evalkit/core/runner.py` (`RunnerDeps.credentials: KeySource`)
- Test: `tests/test_config.py`, `tests/test_credentials.py`, `tests/db/test_keys.py`

```python
class KeySource(Protocol):
    def key_for(self, kind: Kind, ref: str) -> str | None: ...
    def require_judge_key(self, base_url: str) -> str | None: ...

class StoredKeys:                     # db/keys.py
    def __init__(self, keys: dict[tuple[Kind, str], str]): ...
    def key_for(self, kind, ref): return self._keys.get((kind, ref))
    def require_judge_key(self, base_url): return self._keys.get(("llm_provider", base_url))

class NoKeysLoaded:                   # app default; any call is a bug
    def key_for(...): raise RuntimeError("server keys are loaded per experiment")

class KeyLoader:
    async def load(self, agent: AgentDefinition, judge_base_url: str | None) -> StoredKeys
        # agent key, every evaluator key in agent.workspace, the provider key
```

### Task 4: Server call sites

**Files:** `src/evalkit/api/app.py`, `api/experiments.py`, `api/harness_check.py`,
`api/imports.py`, `api/runs.py`, `api/meta.py`, `api/deps.py`
- `app.state.keys = KeyLoader(session_factory, box)`; `app.state.llm_providers`.
- Startup: refuse when any sealed key exists and `encryption_key` is unset.
- `create_experiment` / `resume` / `evaluate`: judge = body.judge or
  `default_judge()` (400 when neither); server-driven → K11 check, endpoint
  checks for harness, evaluators and provider, load keys, K9 warnings returned
  in the response as `warnings: list[str]` and stored on the experiment config
  snapshot (`snapshot.warnings`); run with `replace(deps, credentials=keys)`.
- `harness_check`, `imports`, `runs`: load keys for the agent.
- `GET /api/judge-default` → DB (404 when unset); `PUT` (admin).

Test: `tests/api/test_server_keys.py` (create with stored key reaches the fake
harness as `x-api-key`; no key → warning; unregistered judge → 400; no default
judge → 400; judge-default GET/PUT).

### Task 5: API for keys and providers

**Files:** `api/agents.py`, `api/evaluators.py`, create `api/llm_providers.py`,
register in `app.py`.
- `api_key` in create/patch bodies; `has_api_key` in views; personal → 400;
  no `encryption_key` → 400; URL change → `check_endpoint` + key cleared.
- `/api/llm-providers` CRUD, admin-only writes; `/api/evaluators/{ref}/restore`.

Test: `tests/api/test_api_keys.py`.

### Task 6: CLI

**Files:** `src/evalkit/cli/admin_commands.py`, `src/evalkit/cli/main.py`,
`src/evalkit/cli/server_commands.py`, `src/evalkit/cli/local_run.py`,
`src/evalkit/sdk/runner.py`
- `--api-key-env NAME` / `--clear-api-key` on agents and evaluators create/edit;
  `evaluators show|restore`; `llm-providers create|edit|list|show|archive|restore`;
  `judge-default show|set`; `server rekey` replaces `trace-sources rekey`.
- K13 note in the help of every URL option.
- K10 warning line for local runs.

Test: `tests/cli/test_key_commands.py`.

### Task 7: UI

**Files:** `ui/src/agents/AgentForm.tsx`, `ui/src/lib/agentForm.ts`, new
`ui/src/evaluators/EvaluatorsPage.tsx`, new `ui/src/pages/LlmProvidersPage.tsx`,
`ui/src/evals/RunLauncher.tsx`, `ui/src/pages/TraceSourcesPage.tsx`, routes and
nav in `ui/src/app.tsx` / `ui/src/shell/Shell.tsx`, a shared
`ui/src/lib/EndpointNote.tsx` and `ApiKeyField.tsx`.

### Task 8: Try-it stack, e2e, docs, changelog

- `skills/evalkit-deployment/try/`: config drops `judge_default`/`credentials`,
  adds `allow_private_endpoints: true`; compose `setup` service; `evalkit.yaml`
  rename.
- `skills/evalkit-deployment/config.example.yaml` and references, `evalkit-usage`
  skill, `docs/user/*`, `docs/flows/*`, AGENTS.md terms, `CHANGELOG.md`.
- `uv run python scripts/gen_schemas.py`.
- Full check: `uv run ruff check`, `uv run ruff format --check`,
  `uv run mypy src/evalkit`, `uv run pytest -n auto`, `cd ui && npm test && npx tsc -b`.
