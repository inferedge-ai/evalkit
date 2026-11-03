# OIDC SSO and tenancy: plan overview

**Spec:** `docs/superpowers/specs/2026-11-03-oidc-sso-and-tenancy-design.md`

Three plans, three PRs, in this order. Each produces working software on
its own and is merged before the next starts.

| Plan | File | Delivers |
| --- | --- | --- |
| 1 | `01-tenancy.md` | `tenants` table, `tenant_id` on the six root tables, migration `0016`, tenant on `Actor` and `WorkspaceRef`, scoped stores, the session guard, pre-tenant lookups, blob keys under `tenants/<id>/`, `evalkit blobs relocate`. No visible change for a single installation. |
| 2 | `02-server-cli-sso.md` | `auth.oidc` config, discovery and JWKS cache, the request verifier, `POST /api/auth/oidc/register`, `users bind` and `users add --subject`, `password_login`, MCP on the shared verifier, `client/oidc.py` with authlib, `evalkit login` loopback and device flows, client credentials for CI, `EVALKIT_OTEL_TOKEN` required, docs and skills. |
| 3 | `03-ui-sso.md` | `oidc-client-ts`, the login page with the SSO button, `/login/callback`, the bearer function in `lib/api.ts`, logout, browser pass with screenshots. |

## Shared interface contract

Names that cross plan boundaries. A plan may add to this list but must not
rename anything in it.

### Plan 1 produces

- `evalkit.db.session.ALL_TENANTS`: a module-level sentinel object.
- `evalkit.db.session.tenant_session(session_factory, tenant_id: int | object) -> AsyncIterator[AsyncSession]`: async context manager that opens a session with `session.info["tenant_id"]` set. Every store uses it; nothing opens a bare `session_factory()` except `make_session_factory` internals.
- `evalkit.db.session.register_tenant_guard()`: registers the `do_orm_execute` listener on `AsyncSession`'s sync `Session` class; called once by `make_session_factory`.
- `evalkit.core.tenant.resolve_tenant(request, claims: dict | None) -> int`: returns `1`. `create_app(cfg, ..., resolve_tenant=resolve_tenant)` stores it on `app.state.resolve_tenant`.
- `evalkit.core.access.Actor` gains `tenant_id: int` (after `is_admin`, before `roles`). `evalkit.core.access.WorkspaceRef` gains `tenant_id: int` (after `archived`).
- `evalkit.core.access.same_tenant(actor, ws) -> bool`; every `can_*` calls it first.
- Store constructors take `tenant_id: int` as the second positional argument: `UserStore(session_factory, tenant_id)`, `WorkspaceStore(session_factory, tenant_id)`, `AgentStore(session_factory, tenant_id, box)`, `RemoteEvaluatorStore(session_factory, tenant_id, box)`, `LlmProviderStore(session_factory, tenant_id)`, `TraceSourceStore(session_factory, tenant_id, box)`, `TraceImportStore(session_factory, tenant_id)`, `SpanStore(session_factory, tenant_id)`, `MappingStore(session_factory, tenant_id)`, `PgStore(session_factory, tenant_id, ...)`.
- `evalkit.api.access.TenantStores`: frozen dataclass with fields `tenant_id`, `session_factory`, `users`, `workspaces`, `agents`, `evaluators`, `providers`, `sources`, `imports`, `spans`, `mappings`, `tokens`, `keys`, `review`, `sessions`, `blob`, `deps`, `store`, and a `session()` method that opens a scoped session. `evalkit.api.access.build_tenant_stores(state, tenant_id) -> TenantStores` builds one; the dependency `tenant_stores(actor: ActorDep, request)` calls it; `StoresDep = Annotated[TenantStores, Depends(tenant_stores)]`.
- `evalkit.api.access.workspace_ref(stores: TenantStores, name: str) -> WorkspaceRef` and `resolve_user(stores: TenantStores, username: str) -> User`.
- Pre-tenant lookups, each opening `tenant_session(sf, ALL_TENANTS)`: `evalkit.db.auth.find_token(sf, token_hash) -> tuple[AuthToken, int]` (row and tenant id, joined through `users` or `agents`→`workspaces`); `evalkit.db.experiments.tenant_of_experiment(sf, experiment_id) -> int`; `evalkit.db.trace_imports.tenant_of_import(sf, import_id) -> int`; `evalkit.db.users.any_admin_exists(sf) -> bool`.
- The four key functions in `evalkit.core.blob` keep their signatures. The SDK and the client compute keys and send them to the API, so they cannot carry a tenant. `evalkit.core.blob.TenantBlobStore(inner, tenant_id)` wraps the server's `BlobStore`, prefixes `tenants/<id>/` on every operation and strips it on list; `TenantStores.blob` is that wrapper. Stored keys such as `agent_runs.trajectory_key` stay unprefixed.
- `evalkit.core.blob.Blob.has_any(prefix: str) -> bool`.
- `evalkit users add <name>` keeps its password prompt; Plan 2 adds `--subject`.
- `GET /api/workspaces/...` payloads include `tenant_id`.

### Plan 2 produces

- `evalkit.config.OidcConfig` (`issuer`, `client_id`, `audience`, `name`, `scopes`, `authorize_params`, `algorithms`) and `AuthConfig.oidc: OidcConfig | None`, `AuthConfig.password_login: Literal["admin", "all"]`.
- `evalkit.api.oidc.OidcState`: `cfg`, `discovery: Discovery | None`, `keys: JwkCache | None`, `mcp_resource_url: str`; stored on `app.state.oidc` (`None` when `auth.oidc` is absent).
- `evalkit.api.oidc.verify_access_token(oidc: OidcState, token: str, audience: list[str]) -> dict`.
- `evalkit.api.auth.sso_user(state, session, token, audience: list[str], request: Request | None) -> User` (`state` is `app.state`; `request` is `None` from the MCP verifier) and `is_jwt(token) -> bool`.
- `evalkit.client.oidc.TokenSource` (Protocol, `bearer() -> str`), `StaticToken`, `StoredOidcLogin`, `ClientCredentials`, `EvalkitAuth(httpx.Auth)`.
- `evalkit.client.connection.Connection` gains `oidc: OidcLogin | None` and `machine: MachineLogin | None`. `evalkit.client.oidc.build_auth(conn, *, path=None, transport=None) -> EvalkitAuth | None` picks the source (machine, static token, stored SSO login); `evalkit.client.oidc.static_auth(token) -> EvalkitAuth`; `evalkit.client.oidc.fetch_providers(url, transport=None) -> dict`. These live in `client/oidc.py`, not `connection.py`, because `oidc.py` imports `connection` for file IO.
- `SyncApi(server_url, auth: httpx.Auth | None = None, *, transport=None)` and `AsyncApi` the same; the `token: str` parameter is removed.
- `GET /api/auth/providers`, `POST /api/auth/oidc/register`, `POST /api/users/{username}/identity`.

### Plan 3 consumes

- `GET /api/auth/providers` and `POST /api/auth/oidc/register` exactly as Plan 2 defines them; `GET /api/auth/me` with `email` and `kind`.
