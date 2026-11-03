# Server and CLI SSO Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let people sign in to evalkit's CLI through any OIDC identity provider, let CI authenticate as a machine client, and make the server verify the IdP's JWT access tokens on every request, with password login kept under a setting.

**Architecture:** The CLI is a public OAuth client (authorization code + PKCE through authlib, device grant for headless, client credentials for CI) and holds the IdP's tokens in `~/.evalkit/config.toml`. The server is a resource server: `current_user` dispatches on token shape, verifies JWTs against a cached JWKS, resolves the tenant, and finds the `users` row by `(tenant, issuer, subject)`; one idempotent register route creates that row from the ID token. MCP and the collector reuse the same verifier. No evalkit login token is issued for SSO users.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy async, PyJWT (`pyjwt[crypto]`), authlib (httpx client), httpx, typer, fastmcp 4, pytest with `httpx.MockTransport` and a fake IdP.

**Spec:** `docs/superpowers/specs/2026-11-03-oidc-sso-and-tenancy-design.md`, sections "Server configuration, startup and the request verifier", "Registration and user lifecycle", "CLI", "CI and machine users", "SDK and trace export", "Docs, changelog, versioning and tests". Plan overview and the shared names: `docs/superpowers/plans/2026-11-04-oidc-sso-and-tenancy/00-overview.md`. Plan 1 (`01-tenancy.md`) is merged before this plan starts.

## Global Constraints

- Run every tool through `uv run` (`uv run pytest`, `uv run ruff check`, `uv run ruff format`, `uv run mypy src/evalkit`). Manage dependencies with `uv add` / `uv remove`, never by editing `pyproject.toml`.
- No new docstrings or inline comments, except the public `evalkit.sdk` docstrings, which must be updated when behaviour changes. f-strings everywhere except stdlib logging. `X | None`, never `Optional`. No local imports. No `try`/`except` unless this plan names the exception path. New functions start with a verb.
- Layering: `sdk → client → core`; `cli → client → core`; `api → core → store`; `db` may import `core`. `evalkit.sdk` never imports `evalkit.cli`. Shared client code goes in `src/evalkit/client/`.
- Stage explicit paths in every commit (`git add <paths>`), never `git add -A`. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Names from the overview's shared interface contract are fixed: `OidcConfig`, `AuthConfig.oidc`, `AuthConfig.password_login`, `OidcState` on `app.state.oidc`, `verify_access_token(oidc, token, audience)`, `sso_user`, `is_jwt`, `TokenSource`, `StaticToken`, `StoredOidcLogin`, `ClientCredentials`, `EvalkitAuth`, `Connection.oidc`, `GET /api/auth/providers`, `POST /api/auth/oidc/register`, `POST /api/users/{username}/identity`.
- Plan 1 names this plan relies on: `ALL_TENANTS` and `tenant_session` in `evalkit.db.session`; `app.state.resolve_tenant(request, claims) -> int`; `Actor.tenant_id`; `TenantStores`/`StoresDep` in `evalkit.api.access`; `UserStore(session_factory, tenant_id)`; `find_token(sf, token_hash)` in `evalkit.db.auth`; `any_admin_exists(sf)` in `evalkit.db.users`; `bearer_login(session, token)` in `evalkit.api.auth` still returns `(AuthToken, User)` for evalkit tokens.
- The IdP in every test is `tests.identity.FakeIdp` (Task 3). No test reaches a real IdP or the network.
- Timeouts and intervals that this plan fixes: JWKS refetch at most once per 60 s; discovery retry every 30 s; a login refreshes when the access token has under 60 s left; the loopback listener waits 300 s; `jwt.decode` leeway is 30 s.

---

## File structure

| File | Responsibility |
| --- | --- |
| `src/evalkit/config.py` | `OidcConfig`; `AuthConfig.oidc` and `password_login`; the `mcp.oauth` load error |
| `src/evalkit/api/oidc.py` (new) | Discovery fetch, `JwkCache`, `OidcState` with startup and retry, `verify_access_token` |
| `src/evalkit/api/auth.py` | `is_jwt`, `sso_user`, the JWT branch in `current_user`, `GET /api/auth/providers`, `POST /api/auth/oidc/register`, password-login policy, `/me` fields, `collector_principal` on the shared path |
| `src/evalkit/api/app.py` | builds `OidcState`, starts its retry task, passes it to MCP |
| `src/evalkit/api/users.py` | `POST /api/users` with `subject`, `POST /api/users/{username}/identity`, `kind`/`email`/`subject` in the list |
| `src/evalkit/api/mcp/auth.py` | one `EvalkitTokenVerifier` on the shared verifier; `RemoteAuthProvider` when SSO is configured |
| `src/evalkit/db/users.py` | `derive_username`, `UserStore.create` with `subject`, `UserStore.register`, `UserStore.bind`, `MachineUser`, `IdentityConflict`, `UserArchived` |
| `src/evalkit/client/oidc.py` (new) | `TokenSource`, `StaticToken`, `StoredOidcLogin`, `ClientCredentials`, `EvalkitAuth`, `fetch_providers`, `build_auth`, `run_loopback_flow`, `run_device_flow`, `register_identity` |
| `src/evalkit/client/connection.py` | `OidcLogin`, `MachineLogin`, `Connection.oidc` / `Connection.machine`, the `[oidc]` table in the file, `active()` precedence |
| `src/evalkit/client/api.py` | `SyncApi` / `AsyncApi` take `auth: httpx.Auth | None` |
| `src/evalkit/cli/client.py`, `src/evalkit/cli/main.py`, `src/evalkit/cli/local_run.py`, `src/evalkit/sdk/runner.py` | callers pass `auth`; `login`, `whoami`, `users add --subject`, `users bind`, `users list` |
| `src/evalkit/sdk/tracing.py` | `EVALKIT_OTEL_TOKEN` required |
| `tests/identity.py` | `FakeIdp` |
| `tests/e2e/services.py` | the fake IdP served on `:19100` |
| `docs/user/*.md`, `skills/*/SKILL.md`, `skills/evalkit-deployment/config.example.yaml`, `skills/evalkit-deployment/references/config-and-deploy.md`, `AGENTS.md`, `CHANGELOG.md`, `mkdocs.yml` | documentation |

---

### Task 1: Dependencies

**Files:**
- Modify: `pyproject.toml` (through `uv`), `uv.lock`

**Interfaces:**
- Produces: `authlib` importable from the base package; `jwt` (PyJWT with `cryptography`) importable with the `server` extra.

- [ ] **Step 1: Add authlib to the base package and pyjwt[crypto] to the server extra**

```bash
uv add authlib
uv add --optional server "pyjwt[crypto]"
```

- [ ] **Step 2: Verify both import and the lock resolved**

```bash
uv sync --extra server --extra otel
uv run python -c "import authlib, jwt; from authlib.integrations.httpx_client import OAuth2Client; print(authlib.__version__, jwt.__version__)"
```

Expected: two version numbers printed, no error. authlib 1.9.x and PyJWT 2.15.x are the versions this plan was written against.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "Add authlib (base) and pyjwt[crypto] (server) for OIDC

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `auth.oidc` and `auth.password_login` config

**Files:**
- Modify: `src/evalkit/config.py`
- Modify: `skills/evalkit-deployment/config.example.yaml`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `evalkit.config.OidcConfig(issuer, client_id, audience, name, scopes, authorize_params, algorithms)`; `AuthConfig.oidc: OidcConfig | None`; `AuthConfig.password_login: Literal["admin", "all"]`; `AuthConfig.resolve_password_login() -> Literal["admin", "all"]` (returns `"all"` when `oidc` is `None`). `McpConfig` and `McpOAuthConfig` are deleted; `Config.mcp` no longer exists.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from evalkit.config import AuthConfig, Config, OidcConfig

BASE = {
    "server": {"base_url": "http://evalkit.test"},
    "db": {"url": "postgresql+psycopg://x/y"},
    "blob": {"url": "file:///tmp/blob"},
}
OIDC = {
    "issuer": "https://idp.test",
    "client_id": "evalkit-ui",
    "audience": "https://evalkit.test",
    "name": "Okta",
}


def test_oidc_config_defaults():
    cfg = Config.model_validate({**BASE, "auth": {"oidc": OIDC}})
    assert cfg.auth.oidc == OidcConfig(**OIDC)
    assert cfg.auth.oidc.scopes == ["openid", "profile", "email", "offline_access"]
    assert cfg.auth.oidc.authorize_params == {}
    assert cfg.auth.oidc.algorithms == ["RS256"]
    assert cfg.auth.password_login == "admin"


def test_oidc_scopes_must_include_openid():
    with pytest.raises(ValidationError, match="must include openid"):
        Config.model_validate({**BASE, "auth": {"oidc": {**OIDC, "scopes": ["email"]}}})


def test_password_login_is_admin_or_all():
    with pytest.raises(ValidationError):
        Config.model_validate({**BASE, "auth": {"password_login": "nobody"}})
    cfg = Config.model_validate({**BASE, "auth": {"oidc": OIDC, "password_login": "all"}})
    assert cfg.auth.resolve_password_login() == "all"


def test_password_login_is_all_without_an_idp():
    assert AuthConfig().resolve_password_login() == "all"
    assert AuthConfig(password_login="admin").resolve_password_login() == "all"
    assert AuthConfig(oidc=OidcConfig(**OIDC)).resolve_password_login() == "admin"


def test_mcp_oauth_moved_to_auth_oidc():
    with pytest.raises(ValidationError, match="mcp.oauth moved to auth.oidc"):
        Config.model_validate({**BASE, "mcp": {"oauth": {"issuer": "x", "jwks_uri": "y"}}})
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_config.py -k "oidc or password_login or mcp_oauth" -v`
Expected: FAIL with `ImportError: cannot import name 'OidcConfig'`.

- [ ] **Step 3: Implement the config**

In `src/evalkit/config.py`, replace the `AuthConfig`, `McpOAuthConfig` and `McpConfig` classes with:

```python
class OidcConfig(ConfigModel):
    issuer: str
    client_id: str
    audience: str
    name: str
    scopes: list[str] = ["openid", "profile", "email", "offline_access"]
    authorize_params: dict[str, str] = {}
    algorithms: list[str] = ["RS256"]

    @field_validator("scopes")
    @classmethod
    def _scopes_include_openid(cls, value: list[str]) -> list[str]:
        if "openid" not in value:
            raise ValueError("auth.oidc.scopes must include openid")
        return value

    @field_validator("issuer")
    @classmethod
    def _issuer_has_no_trailing_slash(cls, value: str) -> str:
        return value.rstrip("/")


class AuthConfig(ConfigModel):
    token_ttl_hours: int = 720
    password_login: Literal["admin", "all"] = "admin"
    oidc: OidcConfig | None = None

    def resolve_password_login(self) -> Literal["admin", "all"]:
        return "all" if self.oidc is None else self.password_login
```

In `Config`, delete the `mcp: McpConfig = McpConfig()` field and add, below `_keys_moved_to_db`:

```python
    # TODO: remove in 0.6.0, two minor versions after 0.4.0 moved mcp.oauth to auth.oidc.
    @model_validator(mode="before")
    @classmethod
    def _mcp_oauth_moved(cls, data: object) -> object:
        if isinstance(data, dict) and isinstance(data.get("mcp"), dict) and "oauth" in data["mcp"]:
            raise ValueError(
                "mcp.oauth moved to auth.oidc: set auth.oidc.issuer, client_id, audience and "
                "name; /mcp verifies tokens with the same settings as /api"
            )
        return data
```

The version numbers in the TODO follow the pattern on `_keys_moved_to_db` and are filled in at release time.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS. If an existing test constructs `McpConfig`, delete that test's use of it (the class is gone).

- [ ] **Step 5: Update the example config**

In `skills/evalkit-deployment/config.example.yaml` replace the `auth:` block and the commented `mcp:` block with:

```yaml
auth:
  token_ttl_hours: 720 # password login tokens
  # password_login: admin # with oidc set: admin (default) or all; ignored without oidc
  # oidc: # single sign-on through an OIDC identity provider; see evalkit-docs/sso.md
  #   issuer: https://id.example.com # discovery at <issuer>/.well-known/openid-configuration
  #   client_id: 0oa1b2c3d4e5f6g7h8 # the one public client the UI and CLI share; no secret
  #   audience: https://evalkit.example.com # what the IdP puts in access tokens' aud for evalkit
  #   name: Okta # the login button's label
  #   scopes: [openid, profile, email, offline_access] # default
  #   authorize_params: {} # extra authorize-request parameters, e.g. {resource: https://evalkit.example.com} or {audience: ...}
  #   algorithms: [RS256] # default
```

- [ ] **Step 6: Lint, type-check, commit**

```bash
uv run ruff check src/evalkit/config.py tests/test_config.py && uv run ruff format src/evalkit/config.py tests/test_config.py && uv run mypy src/evalkit
git add src/evalkit/config.py tests/test_config.py skills/evalkit-deployment/config.example.yaml
git commit -m "Config: auth.oidc and auth.password_login; mcp.oauth fails to load

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

`mypy` will fail in `src/evalkit/api/mcp/auth.py` because `cfg.mcp` is gone; that file is rewritten in Task 10. Until then, run `uv run mypy src/evalkit --exclude 'api/mcp/auth.py'` for the check, and note it in the commit body.

---

### Task 3: `FakeIdp` test double

**Files:**
- Modify: `tests/identity.py`
- Test: `tests/test_fake_idp.py` (new)

**Interfaces:**
- Produces: `tests.identity.FakeIdp` with: attributes `issuer`, `audience`, `client_id`, `kid`, `private_key`, `next_login: tuple[str, str, str | None] | None` (sub, email, preferred_username used by the next `/authorize`), `machine_clients: dict[str, tuple[str, str]]` (client_id → (secret, sub)), `device_pending: int`, `access_ttl: int`; methods `discovery() -> dict`, `jwks() -> dict`, `access_token(sub, *, audience=None, expires_in=None, kid=None, issuer=None, extra: dict | None = None) -> str`, `id_token(sub, email, preferred_username=None, *, nonce=None, kid=None, audience=None) -> str`, `handle(method, path, query: dict[str, str], form: dict[str, str]) -> tuple[int, dict[str, str], bytes]`, `transport() -> httpx.MockTransport`, `asgi() -> FastAPI` (Task 17 uses it). Refresh tokens rotate: a used refresh token is refused with `invalid_grant`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fake_idp.py`:

```python
import json
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oauth2.rfc8628 import DEVICE_CODE_GRANT_TYPE

from tests.identity import FakeIdp


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp()


def test_discovery_and_jwks(idp):
    with httpx.Client(transport=idp.transport()) as http:
        disc = http.get(f"{idp.issuer}/.well-known/openid-configuration").json()
        assert disc["issuer"] == idp.issuer
        assert disc["token_endpoint"] == f"{idp.issuer}/token"
        keys = http.get(disc["jwks_uri"]).json()["keys"]
    assert keys[0]["kid"] == idp.kid
    token = idp.access_token("sub-1")
    key = jwt.PyJWKSet.from_dict({"keys": keys})[idp.kid]
    claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=idp.audience, issuer=idp.issuer)
    assert claims["sub"] == "sub-1"


def test_code_flow_with_pkce_and_refresh_rotation(idp):
    idp.next_login = ("sub-1", "ann@example.com", "ann")
    client = OAuth2Client(
        idp.client_id,
        scope="openid email profile offline_access",
        redirect_uri="http://127.0.0.1:1/callback",
        code_challenge_method="S256",
        transport=idp.transport(),
    )
    url, state = client.create_authorization_url(f"{idp.issuer}/authorize", code_verifier="v" * 43)
    with httpx.Client(transport=idp.transport()) as http:
        resp = http.get(url)
    assert resp.status_code == 302
    sent = parse_qs(urlsplit(resp.headers["location"]).query)
    assert sent["state"] == [state]
    tokens = client.fetch_token(f"{idp.issuer}/token", code=sent["code"][0], code_verifier="v" * 43)
    assert set(tokens) >= {"access_token", "id_token", "refresh_token", "expires_in"}
    idtok = jwt.decode(tokens["id_token"], options={"verify_signature": False})
    assert idtok["email"] == "ann@example.com" and idtok["preferred_username"] == "ann"
    first = tokens["refresh_token"]
    rotated = client.refresh_token(f"{idp.issuer}/token", refresh_token=first)
    assert rotated["refresh_token"] != first
    with pytest.raises(Exception, match="invalid_grant"):
        client.refresh_token(f"{idp.issuer}/token", refresh_token=first)


def test_client_credentials_and_device_grant(idp):
    idp.machine_clients["ci"] = ("s3cret", "machine-sub")
    client = OAuth2Client("ci", "s3cret", transport=idp.transport())
    tokens = client.fetch_token(f"{idp.issuer}/token", grant_type="client_credentials")
    assert jwt.decode(tokens["access_token"], options={"verify_signature": False})["sub"] == "machine-sub"

    idp.next_login = ("sub-2", "bob@example.com", None)
    idp.device_pending = 1
    public = OAuth2Client(idp.client_id, transport=idp.transport())
    with httpx.Client(transport=idp.transport()) as http:
        started = http.post(f"{idp.issuer}/device", data={"client_id": idp.client_id, "scope": "openid"}).json()
    assert {"device_code", "user_code", "verification_uri", "interval"} <= set(started)
    with pytest.raises(Exception, match="authorization_pending"):
        public.fetch_token(f"{idp.issuer}/token", grant_type=DEVICE_CODE_GRANT_TYPE, device_code=started["device_code"])
    tokens = public.fetch_token(f"{idp.issuer}/token", grant_type=DEVICE_CODE_GRANT_TYPE, device_code=started["device_code"])
    assert jwt.decode(tokens["id_token"], options={"verify_signature": False})["sub"] == "sub-2"


async def test_asgi_adapter_serves_discovery(idp):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=idp.asgi()), base_url=idp.issuer) as http:
        disc = (await http.get("/.well-known/openid-configuration")).json()
    assert disc["jwks_uri"] == f"{idp.issuer}/jwks"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_fake_idp.py -v`
Expected: FAIL with `ImportError: cannot import name 'FakeIdp'`.

- [ ] **Step 3: Implement `FakeIdp`**

Append to `tests/identity.py`:

```python
import base64
import json
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request, Response
from jwt.algorithms import RSAAlgorithm

FORM = "application/x-www-form-urlencoded"


class FakeIdp:
    def __init__(
        self,
        issuer: str = "https://idp.test",
        audience: str = "https://evalkit.test",
        client_id: str = "evalkit-ui",
        kid: str = "k1",
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.client_id = client_id
        self.kid = kid
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.next_login: tuple[str, str, str | None] | None = None
        self.machine_clients: dict[str, tuple[str, str]] = {}
        self.device_pending = 0
        self.access_ttl = 3600
        self.codes: dict[str, dict] = {}
        self.refresh_tokens: dict[str, dict] = {}
        self.devices: dict[str, dict] = {}
        self.jwks_fetches = 0

    def discovery(self) -> dict:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.issuer}/authorize",
            "token_endpoint": f"{self.issuer}/token",
            "jwks_uri": f"{self.issuer}/jwks",
            "device_authorization_endpoint": f"{self.issuer}/device",
        }

    def jwks(self) -> dict:
        public = json.loads(RSAAlgorithm.to_jwk(self.private_key.public_key()))
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def _pem(self) -> bytes:
        return self.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def _sign(self, claims: dict, kid: str | None) -> str:
        return jwt.encode(claims, self._pem(), algorithm="RS256", headers={"kid": kid or self.kid})

    def access_token(
        self,
        sub: str,
        *,
        audience: str | None = None,
        expires_in: int | None = None,
        kid: str | None = None,
        issuer: str | None = None,
        extra: dict | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": issuer or self.issuer,
            "sub": sub,
            "aud": audience or self.audience,
            "iat": now,
            "exp": now + (self.access_ttl if expires_in is None else expires_in),
            **(extra or {}),
        }
        return self._sign(claims, kid)

    def id_token(
        self,
        sub: str,
        email: str | None,
        preferred_username: str | None = None,
        *,
        nonce: str | None = None,
        kid: str | None = None,
        audience: str | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "sub": sub,
            "aud": audience or self.client_id,
            "iat": now,
            "exp": now + 300,
        }
        if email is not None:
            claims["email"] = email
        if preferred_username is not None:
            claims["preferred_username"] = preferred_username
        if nonce is not None:
            claims["nonce"] = nonce
        return self._sign(claims, kid)

    def _issue(self, login: dict) -> dict:
        refresh = secrets.token_urlsafe(16)
        self.refresh_tokens[refresh] = login
        return {
            "access_token": self.access_token(login["sub"]),
            "id_token": self.id_token(login["sub"], login["email"], login["preferred_username"], nonce=login.get("nonce")),
            "refresh_token": refresh,
            "expires_in": self.access_ttl,
            "token_type": "Bearer",
        }

    def _login(self) -> dict:
        assert self.next_login is not None, "set idp.next_login before the authorize step"
        sub, email, preferred = self.next_login
        return {"sub": sub, "email": email, "preferred_username": preferred}

    def handle(
        self, method: str, path: str, query: dict[str, str], form: dict[str, str]
    ) -> tuple[int, dict[str, str], bytes]:
        if path == "/.well-known/openid-configuration":
            return 200, {"content-type": "application/json"}, json.dumps(self.discovery()).encode()
        if path == "/jwks":
            self.jwks_fetches += 1
            return 200, {"content-type": "application/json"}, json.dumps(self.jwks()).encode()
        if path == "/authorize":
            code = secrets.token_urlsafe(8)
            self.codes[code] = {**self._login(), "challenge": query.get("code_challenge"), "nonce": query.get("nonce")}
            location = f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"
            return 302, {"location": location}, b""
        if path == "/device":
            device_code = secrets.token_urlsafe(8)
            self.devices[device_code] = {**self._login(), "pending": self.device_pending}
            body = {
                "device_code": device_code,
                "user_code": "ABCD-EFGH",
                "verification_uri": f"{self.issuer}/device/verify",
                "interval": 0,
                "expires_in": 300,
            }
            return 200, {"content-type": "application/json"}, json.dumps(body).encode()
        if path == "/token":
            return self._token(form)
        return 404, {}, b""

    def _token(self, form: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
        grant = form.get("grant_type")
        headers = {"content-type": "application/json"}
        if grant == "authorization_code":
            login = self.codes.pop(form.get("code", ""), None)
            if login is None:
                return 400, headers, b'{"error": "invalid_grant"}'
            verifier = form.get("code_verifier", "")
            digest = hashlib.sha256(verifier.encode()).digest()
            challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            if login["challenge"] and challenge != login["challenge"]:
                return 400, headers, b'{"error": "invalid_grant", "error_description": "pkce"}'
            return 200, headers, json.dumps(self._issue(login)).encode()
        if grant == "refresh_token":
            login = self.refresh_tokens.pop(form.get("refresh_token", ""), None)
            if login is None:
                return 400, headers, b'{"error": "invalid_grant"}'
            return 200, headers, json.dumps(self._issue(login)).encode()
        if grant == "client_credentials":
            client_id, secret = form.get("client_id"), form.get("client_secret")
            if client_id not in self.machine_clients or self.machine_clients[client_id][0] != secret:
                return 401, headers, b'{"error": "invalid_client"}'
            body = {"access_token": self.access_token(self.machine_clients[client_id][1]), "expires_in": self.access_ttl, "token_type": "Bearer"}
            return 200, headers, json.dumps(body).encode()
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            pending = self.devices.get(form.get("device_code", ""))
            if pending is None:
                return 400, headers, b'{"error": "expired_token"}'
            if pending["pending"] > 0:
                pending["pending"] -= 1
                return 400, headers, b'{"error": "authorization_pending"}'
            return 200, headers, json.dumps(self._issue(pending)).encode()
        return 400, headers, b'{"error": "unsupported_grant_type"}'

    def _handle_httpx(self, request: httpx.Request) -> httpx.Response:
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()} if request.content else {}
        if request.headers.get("authorization", "").startswith("Basic "):
            user, _, password = base64.b64decode(request.headers["authorization"][6:]).decode().partition(":")
            form.setdefault("client_id", user)
            form.setdefault("client_secret", password)
        status, headers, body = self.handle(request.method, request.url.path, query, form)
        return httpx.Response(status, headers=headers, content=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle_httpx)

    def asgi(self) -> FastAPI:
        app = FastAPI()

        @app.api_route("/{path:path}", methods=["GET", "POST"])
        async def serve(path: str, request: Request) -> Response:
            query = dict(request.query_params)
            form = dict(await request.form()) if request.headers.get("content-type", "").startswith(FORM) else {}
            if request.headers.get("authorization", "").startswith("Basic "):
                user, _, password = base64.b64decode(request.headers["authorization"][6:]).decode().partition(":")
                form.setdefault("client_id", user)
                form.setdefault("client_secret", password)
            status, headers, body = self.handle(request.method, f"/{path}", query, {k: str(v) for k, v in form.items()})
            return Response(content=body, status_code=status, headers=headers)

        return app
```

Add `import hashlib` to the import block. authlib's `OAuth2Client` sends the client secret with HTTP Basic by default, which is why `_handle_httpx` and `asgi` fold the `Authorization: Basic` header into the form.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_fake_idp.py -v`
Expected: PASS, four tests. If `test_code_flow_with_pkce_and_refresh_rotation` fails on the second refresh with a different exception type, change the `match` to `"invalid_grant"` on `authlib.integrations.base_client.errors.OAuthError`; that is the class `fetch_token` raises for an error body.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check tests/identity.py tests/test_fake_idp.py && uv run ruff format tests/identity.py tests/test_fake_idp.py
git add tests/identity.py tests/test_fake_idp.py
git commit -m "Tests: FakeIdp with discovery, JWKS, code, refresh, client credentials and device grants

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Discovery, JWKS cache and `verify_access_token`

**Files:**
- Create: `src/evalkit/api/oidc.py`
- Test: `tests/api/test_oidc_verify.py` (new)

**Interfaces:**
- Consumes: `evalkit.config.OidcConfig`; `tests.identity.FakeIdp`.
- Produces: `evalkit.api.oidc.Discovery` (frozen dataclass: `authorization_endpoint`, `token_endpoint`, `jwks_uri`, `device_authorization_endpoint: str | None`); `JwkCache(jwks_uri, http: httpx.AsyncClient)` with `async signing_key(token) -> jwt.PyJWK`; `OidcState(cfg: OidcConfig, http: httpx.AsyncClient, mcp_resource_url: str)` with `discovery: Discovery | None`, `keys: JwkCache | None`, `async load() -> bool`, `async retry_forever(interval: float = 30)`; `async verify_access_token(oidc: OidcState, token: str, audience: list[str]) -> dict` raising `fastapi.HTTPException` 401 with the reason or 503 while discovery is missing; `IdpUnavailable` is the 503 exception class used by the providers route too.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_oidc_verify.py`:

```python
import httpx
import pytest
from fastapi import HTTPException

from evalkit.api.oidc import JwkCache, OidcState, verify_access_token
from evalkit.config import OidcConfig
from tests.identity import FakeIdp

RESOURCE = "https://evalkit.test/mcp"


def cfg_for(idp: FakeIdp) -> OidcConfig:
    return OidcConfig(issuer=idp.issuer, client_id=idp.client_id, audience=idp.audience, name="Fake")


@pytest.fixture
async def oidc():
    idp = FakeIdp()
    state = OidcState(cfg_for(idp), httpx.AsyncClient(transport=idp.transport()), RESOURCE)
    assert await state.load() is True
    yield idp, state
    await state.http.aclose()


async def test_valid_token_yields_claims(oidc):
    idp, state = oidc
    claims = await verify_access_token(state, idp.access_token("sub-1"), [idp.audience])
    assert claims["sub"] == "sub-1" and claims["iss"] == idp.issuer


@pytest.mark.parametrize(
    "token_kwargs,reason",
    [
        ({"issuer": "https://other.test"}, "issuer"),
        ({"audience": "https://other.test"}, "audience"),
        ({"expires_in": -120}, "expired"),
    ],
)
async def test_bad_claims_are_401_with_the_reason(oidc, token_kwargs, reason):
    idp, state = oidc
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, idp.access_token("sub-1", **token_kwargs), [idp.audience])
    assert info.value.status_code == 401 and reason in info.value.detail.lower()


async def test_only_the_given_audiences_are_accepted(oidc):
    idp, state = oidc
    token = idp.access_token("sub-1", audience=RESOURCE)
    with pytest.raises(HTTPException):
        await verify_access_token(state, token, [idp.audience])
    assert (await verify_access_token(state, token, [idp.audience, RESOURCE]))["sub"] == "sub-1"


async def test_bad_signature_is_401(oidc):
    idp, state = oidc
    other = FakeIdp()
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, other.access_token("sub-1"), [idp.audience])
    assert info.value.status_code == 401


async def test_unknown_kid_refetches_once_then_fails(oidc):
    idp, state = oidc
    before = idp.jwks_fetches
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, idp.access_token("sub-1", kid="k2"), [idp.audience])
    assert info.value.status_code == 401 and "signing key" in info.value.detail
    assert idp.jwks_fetches == before + 1
    with pytest.raises(HTTPException):
        await verify_access_token(state, idp.access_token("sub-1", kid="k2"), [idp.audience])
    assert idp.jwks_fetches == before + 1


async def test_rotated_key_is_picked_up_after_a_refetch(oidc):
    idp, state = oidc
    idp.kid = "k2"
    state.keys.last_fetch = 0.0
    claims = await verify_access_token(state, idp.access_token("sub-1"), [idp.audience])
    assert claims["sub"] == "sub-1"


async def test_discovery_down_means_503_until_it_comes_back():
    idp = FakeIdp()
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        return idp._handle_httpx(request)

    state = OidcState(cfg_for(idp), httpx.AsyncClient(transport=httpx.MockTransport(flaky)), RESOURCE)
    assert await state.load() is False
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, idp.access_token("sub-1"), [idp.audience])
    assert info.value.status_code == 503
    assert await state.load() is True
    assert (await verify_access_token(state, idp.access_token("sub-1"), [idp.audience]))["sub"] == "sub-1"
    await state.http.aclose()


async def test_a_token_that_is_not_a_jwt_is_401(oidc):
    idp, state = oidc
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, "not.a.jwt", [idp.audience])
    assert info.value.status_code == 401
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_oidc_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evalkit.api.oidc'`.

- [ ] **Step 3: Implement `api/oidc.py`**

```python
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import HTTPException

from evalkit.config import OidcConfig

LOGGER = logging.getLogger(__name__)
DISCOVERY_PATH = "/.well-known/openid-configuration"
JWKS_REFETCH_SECONDS = 60.0
DISCOVERY_RETRY_SECONDS = 30.0
LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ["exp", "iat", "sub"]


class IdpUnavailable(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=503, detail="identity provider unreachable")


@dataclass(frozen=True)
class Discovery:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    device_authorization_endpoint: str | None


class JwkCache:
    def __init__(self, jwks_uri: str, http: httpx.AsyncClient) -> None:
        self.jwks_uri = jwks_uri
        self.http = http
        self.keys: dict[str, jwt.PyJWK] = {}
        self.last_fetch = 0.0
        self.lock = asyncio.Lock()

    async def fetch(self) -> None:
        resp = await self.http.get(self.jwks_uri)
        resp.raise_for_status()
        self.keys = {key.key_id: key for key in jwt.PyJWKSet.from_dict(resp.json()).keys if key.key_id}
        self.last_fetch = time.monotonic()

    async def signing_key(self, token: str) -> jwt.PyJWK:
        kid = jwt.get_unverified_header(token).get("kid")
        if kid is None:
            raise HTTPException(status_code=401, detail="invalid access token: no kid in header")
        async with self.lock:
            if kid not in self.keys and time.monotonic() - self.last_fetch >= JWKS_REFETCH_SECONDS:
                await self.fetch()
        if kid not in self.keys:
            raise HTTPException(status_code=401, detail=f"invalid access token: unknown signing key {kid}")
        return self.keys[kid]


class OidcState:
    def __init__(self, cfg: OidcConfig, http: httpx.AsyncClient, mcp_resource_url: str) -> None:
        self.cfg = cfg
        self.http = http
        self.mcp_resource_url = mcp_resource_url
        self.discovery: Discovery | None = None
        self.keys: JwkCache | None = None

    async def load(self) -> bool:
        try:
            resp = await self.http.get(f"{self.cfg.issuer}{DISCOVERY_PATH}")
            resp.raise_for_status()
            doc = resp.json()
            keys = JwkCache(doc["jwks_uri"], self.http)
            await keys.fetch()
        except httpx.HTTPError as exc:
            LOGGER.warning("identity provider %s unreachable: %s", self.cfg.issuer, exc)
            return False
        self.discovery = Discovery(
            authorization_endpoint=doc["authorization_endpoint"],
            token_endpoint=doc["token_endpoint"],
            jwks_uri=doc["jwks_uri"],
            device_authorization_endpoint=doc.get("device_authorization_endpoint"),
        )
        self.keys = keys
        return True

    async def retry_forever(self, interval: float = DISCOVERY_RETRY_SECONDS) -> None:
        while self.discovery is None:
            await asyncio.sleep(interval)
            await self.load()


async def verify_access_token(oidc: OidcState, token: str, audience: list[str]) -> dict:
    if oidc.keys is None:
        raise IdpUnavailable()
    try:
        key = await oidc.keys.signing_key(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=oidc.cfg.algorithms,
            issuer=oidc.cfg.issuer,
            audience=audience,
            options={"require": REQUIRED_CLAIMS},
            leeway=LEEWAY_SECONDS,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid access token: {exc}") from exc
```

The two `try`/`except` blocks are the paths the spec names: "If the IdP is unreachable at startup the server still starts: it logs the failure, retries" and "401 with the reason on failure". `jwt.decode` reports `Invalid issuer`, `Audience doesn't match`, `Signature has expired` and `Signature verification failed`, which the tests match on.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/test_oidc_verify.py -v`
Expected: PASS. If the `expired` test fails on wording, print `info.value.detail` and match on the word PyJWT uses (`expired`).

- [ ] **Step 5: Lint, type-check, commit**

```bash
uv run ruff check src/evalkit/api/oidc.py tests/api/test_oidc_verify.py && uv run ruff format src/evalkit/api/oidc.py tests/api/test_oidc_verify.py && uv run mypy src/evalkit --exclude 'api/mcp/auth.py'
git add src/evalkit/api/oidc.py tests/api/test_oidc_verify.py
git commit -m "api/oidc: discovery, JWKS cache and access token verification

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `OidcState` on the app and `GET /api/auth/providers`

**Files:**
- Modify: `src/evalkit/api/app.py` (`create_app` signature, state, lifespan)
- Modify: `src/evalkit/api/auth.py` (new route)
- Test: `tests/api/test_auth_sso.py` (new; later tasks add to it)

**Interfaces:**
- Consumes: `OidcState`, `IdpUnavailable` from Task 4.
- Produces: `create_app(..., oidc_transport: httpx.AsyncBaseTransport | None = None)`; `app.state.oidc: OidcState | None`; `app.state.oidc_retry: asyncio.Task | None`; `GET /api/auth/providers` returning `{"password": "admin" | "all", "oidc": null | {name, issuer, client_id, audience, scopes, authorize_params, authorization_endpoint, token_endpoint, device_authorization_endpoint}}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_auth_sso.py`:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from evalkit.api.app import create_app
from evalkit.config import OidcConfig
from tests.identity import FakeIdp
from tests.native import HOLDER, Env, seed_env


@asynccontextmanager
async def sso_app(
    env: Env, idp: FakeIdp, *, password_login: str = "admin", transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[httpx.AsyncClient]:
    env.deps.cfg.auth.oidc = OidcConfig(
        issuer=idp.issuer, client_id=idp.client_id, audience=idp.audience, name="Fake"
    )
    env.deps.cfg.auth.password_login = password_login
    app = create_app(
        env.deps.cfg,
        blob_store=env.blob,
        runner_deps=env.deps,
        agents=env.deps.agents,
        session_factory=env.session_factory,
        check_schema=False,
        holder=HOLDER,
        oidc_transport=transport or idp.transport(),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp(audience="http://evalkit.test")


@pytest.fixture
async def env(tmp_path, stub_judges, session_factory) -> Env:
    return await seed_env(session_factory, tmp_path)


async def test_providers_without_an_idp(env, tmp_path):
    from tests.native import serve

    async with serve(env, tmp_path) as client:
        resp = await client.get("/api/auth/providers")
    assert resp.status_code == 200
    assert resp.json() == {"password": "all", "oidc": None}


async def test_providers_with_an_idp(env, idp):
    async with sso_app(env, idp) as client:
        resp = await client.get("/api/auth/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert body["password"] == "admin"
    assert body["oidc"] == {
        "name": "Fake",
        "issuer": idp.issuer,
        "client_id": idp.client_id,
        "audience": idp.audience,
        "scopes": ["openid", "profile", "email", "offline_access"],
        "authorize_params": {},
        "authorization_endpoint": f"{idp.issuer}/authorize",
        "token_endpoint": f"{idp.issuer}/token",
        "device_authorization_endpoint": f"{idp.issuer}/device",
    }


async def test_providers_is_503_while_discovery_is_down(env, idp):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    async with sso_app(env, idp, transport=httpx.MockTransport(down)) as client:
        resp = await client.get("/api/auth/providers")
        assert resp.status_code == 503
        assert client.app.state.oidc_retry is not None and not client.app.state.oidc_retry.done()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_auth_sso.py -v`
Expected: FAIL with `TypeError: create_app() got an unexpected keyword argument 'oidc_transport'`.

- [ ] **Step 3: Build the state in `create_app`**

In `src/evalkit/api/app.py`:

- Add `import httpx` and `from evalkit.api.oidc import OidcState` and `from evalkit.api.mcp.auth import MCP_PATH`.
- Add the keyword parameter `oidc_transport: httpx.AsyncBaseTransport | None = None` to `create_app`, and delete the `mcp_jwt_verifier` parameter (Task 10 rewrites MCP auth; delete it now and fix `mount_mcp(app, mcp_jwt_verifier)` to `mount_mcp(app)` at the same time, with `mount_mcp` and `build_mcp` losing their `jwt_verifier` parameter in `api/mcp/server.py`).
- After `app.state.import_tasks = {}` add:

```python
    app.state.oidc = (
        OidcState(
            cfg.auth.oidc,
            httpx.AsyncClient(transport=oidc_transport, timeout=10.0),
            f"{cfg.server.base_url.rstrip('/')}{MCP_PATH}",
        )
        if cfg.auth.oidc is not None
        else None
    )
    app.state.oidc_retry = None
```

- In `lifespan`, after `await bootstrap_admin(...)` and before the reaper is created:

```python
        if app.state.oidc is not None and not await app.state.oidc.load():
            app.state.oidc_retry = asyncio.create_task(app.state.oidc.retry_forever())
```

and in the teardown, after the reaper is cancelled:

```python
        if app.state.oidc_retry is not None:
            app.state.oidc_retry.cancel()
            await asyncio.gather(app.state.oidc_retry, return_exceptions=True)
        if app.state.oidc is not None:
            await app.state.oidc.http.aclose()
```

- [ ] **Step 4: Add the providers route**

In `src/evalkit/api/auth.py`, add `from evalkit.api.oidc import IdpUnavailable` and, above `login`:

```python
@router.get("/providers")
async def providers(request: Request) -> dict:
    cfg = request.app.state.cfg
    oidc = request.app.state.oidc
    body: dict = {"password": cfg.auth.resolve_password_login(), "oidc": None}
    if oidc is None:
        return body
    if oidc.discovery is None:
        raise IdpUnavailable()
    body["oidc"] = {
        "name": oidc.cfg.name,
        "issuer": oidc.cfg.issuer,
        "client_id": oidc.cfg.client_id,
        "audience": oidc.cfg.audience,
        "scopes": oidc.cfg.scopes,
        "authorize_params": oidc.cfg.authorize_params,
        "authorization_endpoint": oidc.discovery.authorization_endpoint,
        "token_endpoint": oidc.discovery.token_endpoint,
        "device_authorization_endpoint": oidc.discovery.device_authorization_endpoint,
    }
    return body
```

`tests/api/test_auth.py` builds a bare `FastAPI()` with only the auth router and a `FakeCfg`; give `FakeCfg` an `auth = AuthConfig()` (it already does) and set `application.state.oidc = None` in its `app` fixture so the route works there too.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/api/test_auth_sso.py tests/api/test_auth.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
uv run ruff check src/evalkit/api tests/api/test_auth_sso.py && uv run ruff format src/evalkit/api tests/api/test_auth_sso.py tests/api/test_auth.py
git add src/evalkit/api/app.py src/evalkit/api/auth.py src/evalkit/api/mcp/server.py tests/api/test_auth_sso.py tests/api/test_auth.py
git commit -m "Server builds OidcState at startup and serves GET /api/auth/providers

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

`api/mcp/auth.py` still references `jwt_verifier`; `mount_mcp(app)` passes nothing, so `build_auth(cfg, session_factory)` is called with two arguments. Until Task 10 lands, temporarily make `build_auth` ignore `cfg.mcp` by returning `EvalkitTokenVerifier(session_factory, cfg.server.base_url, required_scopes=[SCOPE_READ])` unconditionally. Include that two-line change in this commit so the suite runs.

---

### Task 6: The JWT branch in `current_user`, `sso_user`, `/me`

**Files:**
- Modify: `src/evalkit/api/auth.py`
- Test: `tests/api/test_auth_sso.py`

**Interfaces:**
- Consumes: `verify_access_token`, `IdpUnavailable`; `app.state.resolve_tenant(request, claims)` from plan 1; `User.kind` from plan 1; `bearer_login(session, token)` unchanged.
- Produces: `is_jwt(token: str) -> bool`; `async sso_user(state, session, token, audience: list[str], request: Request | None) -> User` where `state` is `app.state`; `current_user` serves SSO requests; `GET /api/auth/me` answers `email` and `kind`.

The overview lists `sso_user(request, session, token)`. It takes `state` and `audience` as well because the MCP verifier (Task 10) has no request and a different audience list; `request` is passed through to `resolve_tenant`. Plan 3 does not call `sso_user`, so the extra parameters change nothing downstream.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_auth_sso.py`:

```python
from evalkit.db.models import User
from evalkit.db.users import UserStore


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def bind_user(env: Env, idp: FakeIdp, username: str, sub: str, email: str | None = None) -> None:
    store = UserStore(env.session_factory, 1)
    await store.create(username, issuer=idp.issuer, subject=sub, email=email)


async def test_an_access_token_for_a_bound_subject_is_served(env, idp):
    await bind_user(env, idp, "ann", "sub-ann", "ann@example.com")
    async with sso_app(env, idp) as client:
        resp = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "ann" and body["email"] == "ann@example.com" and body["kind"] == "human"
    assert body["token_expires_at"] is not None


async def test_an_unknown_subject_is_401_not_registered(env, idp):
    async with sso_app(env, idp) as client:
        resp = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-x")))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "not registered: subject sub-x has no user"


async def test_an_archived_subject_is_401(env, idp):
    await bind_user(env, idp, "ann", "sub-ann", "ann@example.com")
    await UserStore(env.session_factory, 1).archive("ann")
    async with sso_app(env, idp) as client:
        resp = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
    assert resp.status_code == 401 and "archived" in resp.json()["detail"]


async def test_an_mcp_audience_token_is_refused_on_api(env, idp):
    await bind_user(env, idp, "ann", "sub-ann", "ann@example.com")
    async with sso_app(env, idp) as client:
        token = idp.access_token("sub-ann", audience="http://evalkit.test/mcp")
        resp = await client.get("/api/auth/me", headers=bearer(token))
    assert resp.status_code == 401 and "audience" in resp.json()["detail"].lower()


async def test_evalkit_tokens_still_take_the_hash_path(env, idp):
    async with sso_app(env, idp) as client:
        login = await client.post("/api/auth/login", json={"username": "admin", "password": "hunter2-pw"})
        assert login.status_code == 200
        resp = await client.get("/api/auth/me", headers=bearer(login.json()["token"]))
    assert resp.status_code == 200 and resp.json()["username"] == "admin"
    assert resp.json()["kind"] == "human" and resp.json()["email"] is None


async def test_a_jwt_without_an_idp_configured_is_401(env, tmp_path, idp):
    from tests.native import serve

    async with serve(env, tmp_path) as client:
        resp = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
    assert resp.status_code == 401 and "SSO is not configured" in resp.json()["detail"]


async def test_sso_requests_are_503_while_discovery_is_down(env, idp):
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    await bind_user(env, idp, "ann", "sub-ann", "ann@example.com")
    async with sso_app(env, idp, transport=httpx.MockTransport(down)) as client:
        resp = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
        assert resp.status_code == 503
        login = await client.post("/api/auth/login", json={"username": "admin", "password": "hunter2-pw"})
        assert login.status_code == 200
```

`seed_env` creates `admin` with password `hunter2-pw` (see `tests/native.py` `USERS`).

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_auth_sso.py -v`
Expected: the new tests FAIL (a JWT bearer reaches `_valid_token` and gets `401 invalid token`; `/me` has no `email`).

- [ ] **Step 3: Implement the branch**

In `src/evalkit/api/auth.py`:

```python
from evalkit.api.oidc import IdpUnavailable, verify_access_token


def is_jwt(token: str) -> bool:
    return token.count(".") == 2


async def sso_user(
    state, session: AsyncSession, token: str, audience: list[str], request: Request | None
) -> User:
    oidc = state.oidc
    if oidc is None:
        raise HTTPException(status_code=401, detail="SSO is not configured on this server")
    claims = await verify_access_token(oidc, token, audience)
    tenant_id = state.resolve_tenant(request, claims)
    user = await session.scalar(
        select(User).where(
            User.tenant_id == tenant_id,
            User.issuer == claims["iss"],
            User.subject == claims["sub"],
        )
    )
    if user is None:
        raise HTTPException(
            status_code=401, detail=f"not registered: subject {claims['sub']} has no user"
        )
    if user.archived_at is not None:
        raise HTTPException(status_code=401, detail="user is archived")
    if request is not None:
        request.state.token_expires_at = datetime.fromtimestamp(claims["exp"], UTC)
    return user


def api_audience(state) -> list[str]:
    return [state.oidc.cfg.audience] if state.oidc is not None else []
```

Change `current_user`'s bearer branch from:

```python
    else:
        row, user = await bearer_login(session, _bearer(request))
        request.state.token_expires_at = token_expiry(row)
```

to:

```python
    else:
        token = _bearer(request)
        if is_jwt(token):
            user = await sso_user(
                request.app.state, session, token, api_audience(request.app.state), request
            )
        else:
            row, user = await bearer_login(session, token)
            request.state.token_expires_at = token_expiry(row)
```

Plan 1 made the evalkit-token path open its lookup in an `ALL_TENANTS` session through `find_token`; the JWT path runs in the request session, which plan 1 leaves tenant-less until a user is known. Follow whatever plan 1 did for `bearer_login`'s session and do the same for `sso_user`: the `users` lookup here is by the globally unique `(tenant_id, issuer, subject)` and must run with `session.info["tenant_id"]` set to `ALL_TENANTS` before the tenant is known, then the request session is scoped to `user.tenant_id`.

In `me`, add to the returned dict:

```python
        "email": user.email,
        "kind": user.kind,
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/test_auth_sso.py tests/api/test_auth.py tests/api/test_permissions.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/evalkit/api/auth.py tests/api/test_auth_sso.py && uv run ruff format src/evalkit/api/auth.py tests/api/test_auth_sso.py && uv run mypy src/evalkit --exclude 'api/mcp/auth.py'
git add src/evalkit/api/auth.py tests/api/test_auth_sso.py
git commit -m "current_user verifies IdP access tokens; /me answers email and kind

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Registration

**Files:**
- Modify: `src/evalkit/db/users.py`
- Modify: `src/evalkit/api/auth.py`
- Modify: `src/evalkit/api/app.py` (exception handlers)
- Modify: `tests/identity.py` (`seed_identity` calls `create` with `password=`)
- Test: `tests/db/test_users_identity.py` (new), `tests/api/test_auth_sso.py`

**Interfaces:**
- Produces: `evalkit.db.users.derive_username(preferred_username: str | None, email: str) -> str` (raises `InvalidName`); `UserStore.create(username, *, password=None, issuer=None, subject=None, email=None, is_admin=False) -> User` (exactly one of `password` or `subject`, else `ValueError`); `UserStore.register(issuer, subject, email, preferred_username) -> tuple[User, bool]` raising `IdentityConflict(username, subject)` or `UserArchived(username)`; exception classes `IdentityConflict(Exception)`, `UserArchived(Exception)`; `POST /api/auth/oidc/register` with body `{"id_token": str}` and the access token as bearer, answering `{username, is_admin, email, kind, created}`.

- [ ] **Step 1: Write the failing store tests**

Create `tests/db/test_users_identity.py`:

```python
import pytest

from evalkit.db.users import (
    IdentityConflict,
    InvalidName,
    UserArchived,
    UserStore,
    derive_username,
)

ISSUER = "https://idp.test"


@pytest.mark.parametrize(
    "preferred,email,expected",
    [
        ("ann", "ann@example.com", "ann"),
        ("Ann.Lee@Example.com", "x@example.com", "ann.lee"),
        (None, "Bob_Smith@example.com", "bob_smith"),
        ("okta|ann", "ann@example.com", None),
    ],
)
def test_derive_username(preferred, email, expected):
    if expected is None:
        with pytest.raises(InvalidName):
            derive_username(preferred, email)
    else:
        assert derive_username(preferred, email) == expected


async def test_create_takes_a_password_or_a_subject(session_factory):
    store = UserStore(session_factory, 1)
    with pytest.raises(ValueError, match="password or subject"):
        await store.create("x")
    with pytest.raises(ValueError, match="password or subject"):
        await store.create("x", password="hunter2-pw", subject="s")
    machine = await store.create("ci-bot", issuer=ISSUER, subject="client-1")
    assert machine.password_hash is None and machine.email is None and machine.kind == "machine"
    person = await store.create("ann", password="hunter2-pw")
    assert person.subject is None and person.kind == "human"


async def test_register_creates_once_and_refreshes_email(session_factory):
    store = UserStore(session_factory, 1)
    user, created = await store.register(ISSUER, "sub-1", "ann@example.com", "ann")
    assert created and user.username == "ann" and user.kind == "human"
    again, created = await store.register(ISSUER, "sub-1", "ann@new.example.com", "ann")
    assert not created and again.id == user.id and again.email == "ann@new.example.com"
    workspaces = [w.name for w in await __import__("evalkit.db.workspaces", fromlist=["WorkspaceStore"]).WorkspaceStore(session_factory, 1).list()]
    assert "ann" in workspaces


async def test_register_refuses_a_taken_name_and_an_archived_user(session_factory):
    store = UserStore(session_factory, 1)
    await store.create("ann", password="hunter2-pw")
    with pytest.raises(IdentityConflict, match="taken by a user or workspace"):
        await store.register(ISSUER, "sub-2", "ann@example.com", "ann")
    user, _ = await store.register(ISSUER, "sub-3", "cy@example.com", "cy")
    await store.archive("cy")
    with pytest.raises(UserArchived):
        await store.register(ISSUER, "sub-3", "cy@example.com", "cy")
```

Replace the `__import__` line with a plain `from evalkit.db.workspaces import WorkspaceStore` at the top of the file and `WorkspaceStore(session_factory, 1).list()`.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/db/test_users_identity.py -v`
Expected: FAIL with `ImportError` for `derive_username`.

- [ ] **Step 3: Implement the store side**

In `src/evalkit/db/users.py`:

```python
class IdentityConflict(Exception):
    def __init__(self, username: str, subject: str):
        super().__init__(
            f"username {username} is taken by a user or workspace; "
            f"ask an admin to bind subject {subject} to an account"
        )
        self.username = username
        self.subject = subject


class UserArchived(Exception):
    def __init__(self, username: str):
        super().__init__("user is archived")
        self.username = username


def derive_username(preferred_username: str | None, email: str) -> str:
    source = preferred_username or email
    username = source.split("@")[0].lower()
    check_name(username)
    return username
```

Change `UserStore.create`:

```python
    async def create(
        self,
        username: str,
        *,
        password: str | None = None,
        issuer: str | None = None,
        subject: str | None = None,
        email: str | None = None,
        is_admin: bool = False,
    ) -> User:
        check_name(username)
        if (password is None) == (subject is None):
            raise ValueError("give exactly one of password or subject")
        if password is not None:
            check_new_password(password)
        async with tenant_session(self._sf, self._tenant_id) as s:
            await lock_names(s)
            if await name_taken(s, self._tenant_id, username):
                raise NameTaken(username)
            user = User(
                tenant_id=self._tenant_id,
                username=username,
                password_hash=hash_password(password) if password is not None else None,
                issuer=issuer,
                subject=subject,
                email=email,
                is_admin=is_admin,
            )
            s.add(user)
            await s.flush()
            s.add(Workspace(tenant_id=self._tenant_id, name=username, owner_user_id=user.id))
            await s.commit()
            return user
```

(`tenant_session`, `self._tenant_id` and the `name_taken(session, tenant_id, name)` signature are what plan 1 left in this file; keep its exact forms.)

Add `register`:

```python
    async def register(
        self, issuer: str, subject: str, email: str, preferred_username: str | None
    ) -> tuple[User, bool]:
        async with tenant_session(self._sf, self._tenant_id) as s:
            await lock_names(s)
            user = await s.scalar(
                select(User).where(
                    User.tenant_id == self._tenant_id,
                    User.issuer == issuer,
                    User.subject == subject,
                )
            )
            if user is not None:
                if user.archived_at is not None:
                    raise UserArchived(user.username)
                if user.email != email:
                    user.email = email
                    await s.commit()
                return user, False
            username = derive_username(preferred_username, email)
            if await name_taken(s, self._tenant_id, username):
                raise IdentityConflict(username, subject)
            user = User(
                tenant_id=self._tenant_id,
                username=username,
                password_hash=None,
                issuer=issuer,
                subject=subject,
                email=email,
                is_admin=False,
            )
            s.add(user)
            await s.flush()
            s.add(Workspace(tenant_id=self._tenant_id, name=username, owner_user_id=user.id))
            await s.commit()
            return user, True
```

Update every caller of `create` to the keyword form: `tests/identity.py` `seed_identity` (`store.create(name, password=password, is_admin=name in admins)`), `bootstrap_admin` in `api/auth.py` (`UserStore(session_factory, 1).create("admin", password=password, is_admin=True)`), `api/users.py` (Task 9 rewrites it; for now pass `password=body.password`), and any test that calls `create` positionally (`grep -rn "\.create(\"" tests | grep -i userstore`).

- [ ] **Step 4: Run the store tests**

Run: `uv run pytest tests/db/test_users_identity.py tests/api/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing route tests**

Append to `tests/api/test_auth_sso.py`:

```python
async def register(client: httpx.AsyncClient, idp: FakeIdp, sub: str, email: str | None, preferred: str | None = None, **token_kwargs):
    access = idp.access_token(sub, **token_kwargs)
    return await client.post(
        "/api/auth/oidc/register",
        headers=bearer(access),
        json={"id_token": idp.id_token(sub, email, preferred)},
    )


async def test_register_creates_a_user_then_is_a_no_op(env, idp):
    async with sso_app(env, idp) as client:
        first = await register(client, idp, "sub-ann", "ann@example.com", "ann")
        assert first.status_code == 200, first.text
        assert first.json() == {"username": "ann", "is_admin": False, "email": "ann@example.com", "kind": "human", "created": True}
        second = await register(client, idp, "sub-ann", "ann@new.example.com", "ann")
        assert second.json()["created"] is False and second.json()["email"] == "ann@new.example.com"
        me = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
        assert me.json()["username"] == "ann" and me.json()["personal_workspace"] == "ann"


async def test_register_derives_from_an_email_shaped_preferred_username(env, idp):
    async with sso_app(env, idp) as client:
        resp = await register(client, idp, "sub-1", "x@example.com", "Ann.Lee@corp.example.com")
    assert resp.json()["username"] == "ann.lee"


async def test_register_requires_an_email_claim(env, idp):
    async with sso_app(env, idp) as client:
        resp = await register(client, idp, "sub-1", None, "ann")
    assert resp.status_code == 400 and "email claim" in resp.json()["detail"]


async def test_register_refuses_mismatched_subjects(env, idp):
    async with sso_app(env, idp) as client:
        resp = await client.post(
            "/api/auth/oidc/register",
            headers=bearer(idp.access_token("sub-1")),
            json={"id_token": idp.id_token("sub-2", "a@example.com", "a")},
        )
    assert resp.status_code == 400 and "different subjects" in resp.json()["detail"]


async def test_register_refuses_a_taken_username(env, idp):
    async with sso_app(env, idp) as client:
        resp = await register(client, idp, "sub-9", "admin@example.com", "admin")
    assert resp.status_code == 409
    assert resp.json()["detail"] == "username admin is taken by a user or workspace; ask an admin to bind subject sub-9 to an account"


async def test_register_refuses_an_archived_user(env, idp):
    async with sso_app(env, idp) as client:
        assert (await register(client, idp, "sub-cy", "cy@example.com", "cy")).status_code == 200
        await UserStore(env.session_factory, 1).archive("cy")
        resp = await register(client, idp, "sub-cy", "cy@example.com", "cy")
    assert resp.status_code == 401 and "archived" in resp.json()["detail"]


async def test_register_checks_the_id_tokens_own_kid(env, idp):
    async with sso_app(env, idp) as client:
        access = idp.access_token("sub-1")
        resp = await client.post(
            "/api/auth/oidc/register",
            headers=bearer(access),
            json={"id_token": idp.id_token("sub-1", "a@example.com", "a", kid="k2")},
        )
    assert resp.status_code == 401 and "signing key" in resp.json()["detail"]
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/api/test_auth_sso.py -k register -v`
Expected: FAIL with 404 on `/api/auth/oidc/register`.

- [ ] **Step 7: Implement the route**

In `src/evalkit/api/auth.py`:

```python
import jwt

from evalkit.db.users import IdentityConflict, UserArchived, UserStore


class RegisterRequest(BaseModel):
    id_token: str


@router.post("/oidc/register")
async def register_identity(body: RegisterRequest, request: Request) -> dict:
    state = request.app.state
    oidc = state.oidc
    if oidc is None:
        raise HTTPException(status_code=401, detail="SSO is not configured on this server")
    access = await verify_access_token(oidc, _bearer(request), api_audience(state))
    idtok = await verify_id_token(oidc, body.id_token)
    if idtok["sub"] != access["sub"]:
        raise HTTPException(
            status_code=400, detail="id_token and access token name different subjects"
        )
    if "email" not in idtok:
        raise HTTPException(
            status_code=400,
            detail="the ID token has no email claim; ask the IdP admin to grant the email scope",
        )
    tenant_id = state.resolve_tenant(request, access)
    store = UserStore(state.session_factory, tenant_id)
    try:
        user, created = await store.register(
            idtok["iss"], idtok["sub"], idtok["email"], idtok.get("preferred_username")
        )
    except UserArchived as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "username": user.username,
        "is_admin": user.is_admin,
        "email": user.email,
        "kind": user.kind,
        "created": created,
    }
```

The `try`/`except UserArchived` maps the store's exception to the 401 the spec's sequence diagram shows; `IdentityConflict` goes through an app-level 409 handler (next step). Add `verify_id_token` to `src/evalkit/api/oidc.py`:

```python
async def verify_id_token(oidc: OidcState, token: str) -> dict:
    if oidc.keys is None:
        raise IdpUnavailable()
    try:
        key = await oidc.keys.signing_key(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=oidc.cfg.algorithms,
            issuer=oidc.cfg.issuer,
            audience=oidc.cfg.client_id,
            options={"require": REQUIRED_CLAIMS},
            leeway=LEEWAY_SECONDS,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid id_token: {exc}") from exc
```

In `src/evalkit/api/app.py`, import `IdentityConflict` from `evalkit.db.users` and add `@app.exception_handler(IdentityConflict)` to the `conflict` handler stack.

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/api/test_auth_sso.py tests/db/test_users_identity.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
uv run ruff check src/evalkit/api src/evalkit/db/users.py tests && uv run ruff format src/evalkit/api/auth.py src/evalkit/api/oidc.py src/evalkit/api/app.py src/evalkit/db/users.py tests/db/test_users_identity.py tests/api/test_auth_sso.py tests/identity.py && uv run mypy src/evalkit --exclude 'api/mcp/auth.py'
git add src/evalkit/api/auth.py src/evalkit/api/oidc.py src/evalkit/api/app.py src/evalkit/db/users.py tests/db/test_users_identity.py tests/api/test_auth_sso.py tests/identity.py
git commit -m "POST /api/auth/oidc/register creates the user from the ID token

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Password login policy

**Files:**
- Modify: `src/evalkit/api/auth.py` (`login`)
- Test: `tests/api/test_auth_sso.py`

**Interfaces:**
- Consumes: `AuthConfig.resolve_password_login()`.
- Produces: `POST /api/auth/login` answers 403 `password login is for admins on this server; sign in with <name>` for non-admins when the policy is `admin`; a user with no password hash gets the same 401 as a wrong password.

- [ ] **Step 1: Write the failing tests**

Append to `tests/api/test_auth_sso.py`:

```python
async def test_password_login_is_admin_only_by_default_with_an_idp(env, idp):
    async with sso_app(env, idp) as client:
        admin = await client.post("/api/auth/login", json={"username": "admin", "password": "hunter2-pw"})
        assert admin.status_code == 200
        alice = await client.post("/api/auth/login", json={"username": "akshat", "password": "akshat-pw"})
        assert alice.status_code == 403
        assert alice.json()["detail"] == "password login is for admins on this server; sign in with Fake"
        wrong = await client.post("/api/auth/login", json={"username": "akshat", "password": "nope"})
        assert wrong.status_code == 401


async def test_password_login_for_all_when_configured(env, idp):
    async with sso_app(env, idp, password_login="all") as client:
        resp = await client.post("/api/auth/login", json={"username": "akshat", "password": "akshat-pw"})
    assert resp.status_code == 200


async def test_a_user_without_a_password_gets_401(env, idp):
    async with sso_app(env, idp, password_login="all") as client:
        assert (await register(client, idp, "sub-ann", "ann@example.com", "ann")).status_code == 200
        resp = await client.post("/api/auth/login", json={"username": "ann", "password": "anything"})
    assert resp.status_code == 401 and resp.json()["detail"] == "invalid username or password"
```

Check `tests/native.py` `USERS` for the second user's name and password and substitute them for `akshat` / `akshat-pw` if they differ.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_auth_sso.py -k password_login -v`
Expected: the first FAILS (`alice` gets 200), the third FAILS with a `TypeError` from `verify_password(…, None)`.

- [ ] **Step 3: Implement**

Rewrite `login` in `src/evalkit/api/auth.py`:

```python
@router.post("/login")
async def login(body: LoginRequest, request: Request, session: SessionDep) -> dict:
    cfg = request.app.state.cfg
    tenant_id = request.app.state.resolve_tenant(request, None)
    user = (
        await session.execute(
            select(User).where(User.tenant_id == tenant_id, User.username == body.username)
        )
    ).scalar_one_or_none()
    if len(body.password.encode()) > PASSWORD_MAX_BYTES:
        raise HTTPException(
            status_code=400, detail=f"password is longer than {PASSWORD_MAX_BYTES} bytes"
        )
    if user is None or user.password_hash is None:
        verify_password(body.password, _dummy_password_hash())
        raise HTTPException(status_code=401, detail="invalid username or password")
    if not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid username or password")
    if user.archived_at is not None:
        raise HTTPException(status_code=401, detail="user is archived")
    if cfg.auth.resolve_password_login() == "admin" and not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail=f"password login is for admins on this server; sign in with {cfg.auth.oidc.name}",
        )
    ttl = timedelta(hours=cfg.auth.token_ttl_hours)
    token = await issue_token(session, user, ttl)
    return {"token": token, "username": user.username, "is_admin": user.is_admin}
```

Keep the session scoping plan 1 established for this route (the lookup by `(tenant_id, username)` runs before a user is known).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/test_auth_sso.py tests/api/test_auth.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
uv run ruff format src/evalkit/api/auth.py tests/api/test_auth_sso.py && uv run ruff check src/evalkit/api/auth.py
git add src/evalkit/api/auth.py tests/api/test_auth_sso.py
git commit -m "Password login honours auth.password_login; users without a password get 401

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Machine users: `users add --subject`, `users bind`, `users list`, `promote` refusal

**Files:**
- Modify: `src/evalkit/db/users.py` (`bind`, `set_admin`, `MachineUser`)
- Modify: `src/evalkit/api/users.py`
- Modify: `src/evalkit/api/app.py` (handlers)
- Modify: `src/evalkit/cli/main.py` (`users_add`, `users_bind`, `users_list`)
- Test: `tests/db/test_users_identity.py`, `tests/api/test_auth_sso.py`, `tests/cli/test_cli.py`

**Interfaces:**
- Produces: `UserStore.bind(username, issuer, subject) -> None` raising `IdentityConflict` when the subject already belongs to another row; `MachineUser(Exception)` raised by `set_admin(username, True)` on a machine user; `POST /api/users` body `{username, password?: str, subject?: str}`; `POST /api/users/{username}/identity` body `{subject}`; `GET /api/users` rows gain `email`, `kind`, `has_subject`; CLI `evalkit users add <name> [--subject <sub>]`, `evalkit users bind <name> --subject <sub>`, `evalkit users list` columns.

- [ ] **Step 1: Write the failing store tests**

Append to `tests/db/test_users_identity.py`:

```python
from evalkit.db.users import MachineUser


async def test_bind_sets_the_identity_once(session_factory):
    store = UserStore(session_factory, 1)
    await store.create("ann", password="hunter2-pw")
    await store.bind("ann", ISSUER, "sub-ann")
    bound = next(u for u in await store.list() if u.username == "ann")
    assert (bound.issuer, bound.subject, bound.kind) == (ISSUER, "sub-ann", "human")
    await store.create("bob", password="hunter2-pw")
    with pytest.raises(IdentityConflict, match="already bound"):
        await store.bind("bob", ISSUER, "sub-ann")


async def test_promote_refuses_a_machine_user(session_factory):
    store = UserStore(session_factory, 1)
    await store.create("ci-bot", issuer=ISSUER, subject="client-1")
    with pytest.raises(MachineUser):
        await store.set_admin("ci-bot", True)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/db/test_users_identity.py -v`
Expected: FAIL on `ImportError: MachineUser` / `AttributeError: bind`.

- [ ] **Step 3: Implement the store side**

In `src/evalkit/db/users.py`:

```python
class MachineUser(Exception):
    def __init__(self, username: str):
        super().__init__(f"{username} is a machine user and cannot be an admin")


    async def bind(self, username: str, issuer: str, subject: str) -> None:
        async with tenant_session(self._sf, self._tenant_id) as s:
            await lock_names(s)
            user = await self._get(s, username)
            other = await s.scalar(
                select(User.username).where(
                    User.tenant_id == self._tenant_id,
                    User.issuer == issuer,
                    User.subject == subject,
                    User.id != user.id,
                )
            )
            if other is not None:
                raise IdentityConflict.already_bound(subject, other)
            user.issuer = issuer
            user.subject = subject
            await s.commit()
```

Give `IdentityConflict` a second constructor so both messages share one 409 class:

```python
class IdentityConflict(Exception):
    def __init__(self, message: str):
        super().__init__(message)

    @classmethod
    def name_taken(cls, username: str, subject: str) -> IdentityConflict:
        return cls(
            f"username {username} is taken by a user or workspace; "
            f"ask an admin to bind subject {subject} to an account"
        )

    @classmethod
    def already_bound(cls, subject: str, username: str) -> IdentityConflict:
        return cls(f"subject {subject} is already bound to {username}")
```

and change `register` to raise `IdentityConflict.name_taken(username, subject)` (add `from __future__ import annotations` at the top of the module for the return annotation). In `set_admin`, before `user.is_admin = value`:

```python
            if value and user.kind == "machine":
                raise MachineUser(username)
```

- [ ] **Step 4: Run the store tests**

Run: `uv run pytest tests/db/test_users_identity.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing API and CLI tests**

Append to `tests/api/test_auth_sso.py`:

```python
async def test_admin_adds_a_machine_user_and_its_token_is_served(env, idp):
    idp.machine_clients["ci"] = ("s3cret", "client-ci-sub")
    async with sso_app(env, idp) as client:
        unbound = await client.get("/api/auth/me", headers=bearer(idp.access_token("client-ci-sub")))
        assert unbound.status_code == 401 and "subject client-ci-sub" in unbound.json()["detail"]
        created = await client.post("/api/users", json={"username": "ci-bot", "subject": "client-ci-sub"})
        assert created.status_code == 200, created.text
        me = await client.get("/api/auth/me", headers=bearer(idp.access_token("client-ci-sub")))
        assert me.status_code == 200 and me.json()["kind"] == "machine" and me.json()["username"] == "ci-bot"
        rows = (await client.get("/api/users")).json()
        bot = next(r for r in rows if r["username"] == "ci-bot")
        assert bot["kind"] == "machine" and bot["email"] is None and bot["has_subject"] is True
        promote = await client.post("/api/users/ci-bot/promote")
        assert promote.status_code == 400 and "machine user" in promote.json()["detail"]


async def test_admin_binds_an_existing_user(env, idp):
    async with sso_app(env, idp) as client:
        resp = await client.post("/api/users/akshat/identity", json={"subject": "sub-akshat"})
        assert resp.status_code == 200 and resp.json() == {"bound": "akshat", "subject": "sub-akshat"}
        me = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-akshat")))
        assert me.status_code == 200 and me.json()["username"] == "akshat"
        again = await client.post("/api/users/admin/identity", json={"subject": "sub-akshat"})
        assert again.status_code == 409


async def test_binding_needs_an_idp(env, tmp_path):
    from tests.native import serve

    async with serve(env, tmp_path) as client:
        resp = await client.post("/api/users/akshat/identity", json={"subject": "s"})
    assert resp.status_code == 400 and "SSO is not configured" in resp.json()["detail"]
```

`serve` and `sso_app` both log the client in as `admin`; confirm how `serve` sets the bearer in `tests/native.py` and mirror it in `sso_app` (log in with `admin` / `hunter2-pw` and set `client.headers["Authorization"]`). Update `sso_app` in this task so every test above has an admin bearer by default.

Append to `tests/cli/test_cli.py` (the `env` fixture there runs the CLI against a live server as `admin`):

```python
def test_users_add_with_a_subject_skips_the_password(env, monkeypatch):
    result = runner.invoke(cli_app, ["users", "add", "ci-bot", "--subject", "client-1"])
    assert result.exit_code == 1 and "SSO is not configured" in result.output


def test_users_list_shows_kind_and_email(env):
    result = runner.invoke(cli_app, ["users", "list"])
    assert result.exit_code == 0
    assert "human" in result.output
```

The CLI test server has no IdP, so `add --subject` and `bind` answer the server's 400; that is the behaviour under test here. The SSO-configured CLI path is covered end to end in Task 17.

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/api/test_auth_sso.py -k "machine or binds or binding" tests/cli/test_cli.py -k "users_add or users_list" -v`
Expected: FAIL (422 on the create body, 404 on `/identity`, CLI option unknown).

- [ ] **Step 7: Implement the API**

Rewrite `src/evalkit/api/users.py`:

```python
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from evalkit.api.auth import AdminUser
from evalkit.db.users import UserStore

router = APIRouter(prefix="/api/users", tags=["users"])


def _users(request: Request, admin: AdminUser) -> UserStore:
    return UserStore(request.app.state.session_factory, admin.tenant_id)


def _issuer(request: Request) -> str:
    oidc = request.app.state.oidc
    if oidc is None:
        raise HTTPException(status_code=400, detail="SSO is not configured on this server")
    return oidc.cfg.issuer


@router.get("")
async def list_users(request: Request, admin: AdminUser) -> list[dict]:
    return [
        {
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "archived": u.archived_at is not None,
            "created_at": u.created_at.isoformat(),
            "email": u.email,
            "kind": u.kind,
            "has_subject": u.subject is not None,
        }
        for u in await _users(request, admin).list()
    ]


class CreateUserRequest(BaseModel):
    username: str
    password: str | None = None
    subject: str | None = None


@router.post("")
async def create_user(body: CreateUserRequest, request: Request, admin: AdminUser) -> dict:
    if (body.password is None) == (body.subject is None):
        raise HTTPException(status_code=400, detail="give exactly one of password or subject")
    if body.subject is not None:
        await _users(request, admin).create(
            body.username, issuer=_issuer(request), subject=body.subject
        )
    else:
        await _users(request, admin).create(body.username, password=body.password)
    return {"username": body.username}


class BindRequest(BaseModel):
    subject: str


@router.post("/{username}/identity")
async def bind_identity(
    username: str, body: BindRequest, request: Request, admin: AdminUser
) -> dict:
    await _users(request, admin).bind(username, _issuer(request), body.subject)
    return {"bound": username, "subject": body.subject}
```

Keep `archive`, `restore`, `set_password`, `promote` and `demote` as they are, each calling `_users(request, admin)`. In `src/evalkit/api/app.py` add `MachineUser` to the `bad_request` handler stack (400).

- [ ] **Step 8: Implement the CLI**

In `src/evalkit/cli/main.py` replace `users_add` and `users_list`, and add `users_bind`:

```python
@users_app.command("add")
def users_add(
    username: str,
    password: str = typer.Option(None, "--password", help="Prompted for when --subject is not given."),
    subject: str = typer.Option(
        None, "--subject", help="Bind the new user to this identity provider subject: a machine user, no password."
    ),
):
    """Create a user (admin only).

    With --subject the user is a machine user bound to that subject at the identity
    provider: no password, no browser login. Without it, a password user; with SSO
    configured and password_login admin, that user can only sign in once made admin.

    Examples: evalkit users add alice · evalkit users add ci-bot --subject 0oa...
    """
    if subject is not None:
        _client().post("/api/users", json={"username": username, "subject": subject})
    else:
        password = password or typer.prompt("Password", hide_input=True)
        _client().post("/api/users", json={"username": username, "password": password})
    typer.echo(f"created {username}")


@users_app.command("bind")
def users_bind(username: str, subject: str = typer.Option(..., "--subject")):
    """Bind an existing user to an identity provider subject (admin only).

    Resolves a 409 at sign-in, links a password account that predates SSO, or binds
    a machine client to an existing user. Example: evalkit users bind alice --subject 0oa...
    """
    _client().post(f"/api/users/{username}/identity", json={"subject": subject})
    typer.echo(f"bound {username} to subject {subject}")


@users_app.command("list")
def users_list():
    """List users (admin only). Example: evalkit users list"""
    for user in _client().get("/api/users").json():
        flags = " ".join(
            flag
            for flag, on in (
                ("ADMIN", user["is_admin"]),
                ("ARCHIVED", user["archived"]),
                ("SUBJECT", user["has_subject"]),
            )
            if on
        )
        email = user.get("email") or ""
        typer.echo(
            f"{user['username']:<24} {user['kind']:<8} {email:<32} {flags:<22} "
            f"created {user['created_at']}"
        )
```

- [ ] **Step 9: Run the tests**

Run: `uv run pytest tests/api/test_auth_sso.py tests/cli/test_cli.py -k "users or machine or bind" tests/db/test_users_identity.py -v`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
uv run ruff check src/evalkit tests && uv run ruff format src/evalkit/api/users.py src/evalkit/api/app.py src/evalkit/db/users.py src/evalkit/cli/main.py tests/api/test_auth_sso.py tests/cli/test_cli.py tests/db/test_users_identity.py && uv run mypy src/evalkit --exclude 'api/mcp/auth.py'
git add src/evalkit/api/users.py src/evalkit/api/app.py src/evalkit/db/users.py src/evalkit/cli/main.py tests/api/test_auth_sso.py tests/cli/test_cli.py tests/db/test_users_identity.py
git commit -m "Machine users: users add --subject, users bind, kind and email in users list

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 10: MCP on the shared verifier

**Files:**
- Modify: `src/evalkit/api/mcp/auth.py`
- Modify: `src/evalkit/api/mcp/server.py`
- Test: `tests/api/test_mcp.py`

**Interfaces:**
- Consumes: `sso_user`, `is_jwt`, `bearer_login`, `OidcState`.
- Produces: `build_auth(state) -> AuthProvider` where `state` is `app.state`; `EvalkitTokenVerifier(state, base_url, required_scopes)`; `ProviderScopeVerifier`, `MultiAuth` usage and `mcp.oauth` are gone.

- [ ] **Step 1: Replace the OAuth tests**

In `tests/api/test_mcp.py`, delete `oauth_app`, `test_the_providers_own_scope_name_is_the_one_required` and `test_oauth_tokens_reach_mcp_and_evalkit_tokens_still_do`, and the imports of `JWTVerifier`, `RSAKeyPair`, `McpConfig`, `McpOAuthConfig`. Add:

```python
from evalkit.db.users import UserStore
from tests.api.test_auth_sso import sso_app
from tests.identity import FakeIdp


async def test_idp_tokens_reach_mcp_as_their_evalkit_user(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, fake_with_runs())
    idp = FakeIdp(audience="http://evalkit.test")
    await UserStore(session_factory, 1).create("ann", issuer=idp.issuer, subject="sub-ann", email="ann@example.com")
    async with sso_app(env, idp) as http:
        meta = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert meta["authorization_servers"] == [idp.issuer]
        assert meta["resource"] == RESOURCE
        for audience in (idp.audience, RESOURCE):
            assert await mcp_status(http, idp.access_token("sub-ann", audience=audience)) == 200
        assert await mcp_status(http, idp.access_token("sub-ann", audience="https://other")) == 401
        assert await mcp_status(http, idp.access_token("sub-nobody")) == 401
        assert await mcp_status(http, bearer(http)) == 200
        await UserStore(session_factory, 1).archive("ann")
        assert await mcp_status(http, idp.access_token("sub-ann")) == 401


async def test_without_an_idp_mcp_publishes_no_metadata(api):
    client, _ = api
    assert (await client.get("/.well-known/oauth-protected-resource/mcp")).status_code == 404
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/api/test_mcp.py -k "idp_tokens or no_metadata" -v`
Expected: FAIL (metadata missing; JWT refused by the hash lookup).

- [ ] **Step 3: Rewrite `api/mcp/auth.py`**

```python
from fastapi import HTTPException
from fastmcp.server.auth import AccessToken, AuthProvider, RemoteAuthProvider, TokenVerifier

from evalkit.api.auth import bearer_login, is_jwt, sso_user, token_expiry
from evalkit.db.session import ALL_TENANTS, tenant_session

SCOPE_READ = "evalkit:read"
MCP_PATH = "/mcp"


def resource_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}{MCP_PATH}"


class EvalkitTokenVerifier(TokenVerifier):
    def __init__(self, state, base_url: str, required_scopes: list[str] | None = None):
        super().__init__(base_url=base_url, required_scopes=required_scopes)
        self._state = state

    async def verify_token(self, token: str) -> AccessToken | None:
        async with tenant_session(self._state.session_factory, ALL_TENANTS) as session:
            try:
                if is_jwt(token):
                    audience = [self._state.oidc.cfg.audience, self._state.oidc.mcp_resource_url]
                    user = await sso_user(self._state, session, token, audience, None)
                    expires = None
                else:
                    row, user = await bearer_login(session, token)
                    expires = token_expiry(row)
            except HTTPException:
                return None
            await session.commit()
        return AccessToken(
            token=token,
            client_id=f"evalkit:{user.username}",
            scopes=[SCOPE_READ],
            expires_at=int(expires.timestamp()) if expires is not None else None,
            claims={"sub": user.username, "evalkit_user": user.username},
        )


def build_auth(state) -> AuthProvider:
    base_url = state.cfg.server.base_url
    verifier = EvalkitTokenVerifier(state, base_url, required_scopes=[SCOPE_READ])
    if state.oidc is None:
        return verifier
    return RemoteAuthProvider(
        token_verifier=verifier,
        # As configured: AnyHttpUrl would turn https://host into https://host/, which clients
        # cannot match to the provider's issuer (RFC 8414).
        authorization_servers=[state.oidc.cfg.issuer],  # type: ignore[list-item]
        base_url=base_url,
        scopes_supported=state.oidc.cfg.scopes,
        resource_name="evalkit",
    )
```

The `try`/`except HTTPException` is the existing pattern in this file: fastmcp expects `None` for a refused token, not an exception. The one comment kept is the existing one about `AnyHttpUrl`. If `sso_user` under plan 1 wants the users lookup in a specific session form, mirror what `current_user` does.

In `src/evalkit/api/mcp/server.py`, `build_mcp(app)` calls `build_auth(app.state)`; `mount_mcp(app)` has no verifier parameter. The expiry for an SSO token is `None` here rather than the claim's `exp`; fastmcp re-verifies on every request, so expiry is enforced by `verify_access_token`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/api/test_mcp.py -v`
Expected: PASS. `test_mcp_refuses_a_login_token_api_refuses`, `test_agent_tokens_cannot_use_mcp` and `test_download_tickets_cannot_use_mcp` still pass because `bearer_login` keeps refusing agent tokens and tickets.

- [ ] **Step 5: Type-check everything, commit**

```bash
uv run ruff check src/evalkit/api/mcp tests/api/test_mcp.py && uv run ruff format src/evalkit/api/mcp/auth.py src/evalkit/api/mcp/server.py tests/api/test_mcp.py && uv run mypy src/evalkit
git add src/evalkit/api/mcp/auth.py src/evalkit/api/mcp/server.py tests/api/test_mcp.py
git commit -m "MCP verifies tokens with the shared verifier; mcp.oauth scope mapping removed

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

From here on, `uv run mypy src/evalkit` runs without the exclusion.

---

### Task 11: Collector principal on the shared path

**Files:**
- Modify: `src/evalkit/api/auth.py` (`collector_principal`)
- Test: `tests/api/test_otel.py`

**Interfaces:**
- Produces: `collector_principal` accepts an IdP access token for the configured `/api` audience as a user principal; agent tokens unchanged.

- [ ] **Step 1: Write the failing test**

Find the test in `tests/api/test_otel.py` that posts a batch with a user token (search for `login_as` or `Authorization`), and add beside it:

```python
from tests.api.test_auth_sso import sso_app
from tests.identity import FakeIdp
from evalkit.db.users import UserStore


async def test_an_idp_token_can_export_to_its_own_agent(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path)
    idp = FakeIdp(audience="http://evalkit.test")
    await UserStore(session_factory, 1).create("ann", issuer=idp.issuer, subject="sub-ann", email="ann@example.com")
    async with sso_app(env, idp) as client:
        headers = {"Authorization": f"Bearer {idp.access_token('sub-ann')}", "X-Evalkit-Agent": "fake@ann"}
        resp = await client.post("/api/otel/v1/traces", content=EMPTY_BATCH, headers={**headers, "Content-Type": "application/x-protobuf"})
    assert resp.status_code in (200, 404)
```

Use whatever constant the file already uses for an empty or minimal protobuf batch in place of `EMPTY_BATCH`; `404` is acceptable because `ann` has no agent `fake@ann`, the point is that the token is not refused with 401.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/api/test_otel.py -k idp_token -v`
Expected: FAIL with 401.

- [ ] **Step 3: Implement**

In `src/evalkit/api/auth.py`:

```python
async def collector_principal(request: Request, session: SessionDep) -> Principal:
    token = _bearer(request)
    if is_jwt(token):
        user = await sso_user(request.app.state, session, token, api_audience(request.app.state), request)
        await session.commit()
        return Principal(user=user, agent_id=None)
    row = await _valid_bearer_token(session, token)
    if row.kind == TokenKind.AGENT:
        await session.commit()
        return Principal(user=None, agent_id=row.agent_id)
    user = await _active_user(session, row.user_id)
    await session.commit()
    return Principal(user=user, agent_id=None)
```

- [ ] **Step 4: Run the tests and commit**

Run: `uv run pytest tests/api/test_otel.py -v`
Expected: PASS.

```bash
uv run ruff format src/evalkit/api/auth.py tests/api/test_otel.py && uv run ruff check src/evalkit/api/auth.py tests/api/test_otel.py
git add src/evalkit/api/auth.py tests/api/test_otel.py
git commit -m "Collector accepts IdP access tokens for a person; agent tokens unchanged

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 12: `client/oidc.py`: token sources and the auth hook

**Files:**
- Create: `src/evalkit/client/oidc.py`
- Modify: `src/evalkit/client/connection.py` (the data model, done first so the sources can read and write the file)
- Test: `tests/client/test_oidc_sources.py` (new), `tests/cli/test_connection.py`

**Interfaces:**
- Produces in `evalkit.client.connection`: `OidcLogin(NamedTuple)` with `issuer`, `client_id`, `token_endpoint`, `access_token`, `refresh_token`, `expires_at: datetime`; `MachineLogin(NamedTuple)` with `client_id`, `client_secret`; `Connection(url, token, username, from_env=False, oidc: OidcLogin | None = None, machine: MachineLogin | None = None)`; `save` writes the `[oidc]` table; `load` reads it; `active()` precedence: `EVALKIT_SERVER_URL` + `EVALKIT_OIDC_CLIENT_ID` + `EVALKIT_OIDC_CLIENT_SECRET` → `machine`; `EVALKIT_SERVER_URL` + `EVALKIT_TOKEN` → `token`; else the file.
- Produces in `evalkit.client.oidc`: `TokenSource` (Protocol, `bearer() -> str`); `StaticToken(token)`; `StoredOidcLogin(connection: Connection, path: Path | None = None, transport: httpx.BaseTransport | None = None)`; `ClientCredentials(machine: MachineLogin, token_endpoint: str, authorize_params: dict[str, str], transport=None)`; `EvalkitAuth(source: TokenSource)` (an `httpx.Auth`); `LoginExpired(Exception)`; `REFRESH_WINDOW_SECONDS = 60`.
- The overview put `token_source` in `connection`; it lives in `oidc.py` (Task 13) because `oidc.py` imports `connection` for the file and the reverse import would be circular. Callers use `evalkit.client.oidc.build_auth`.

- [ ] **Step 1: Write the failing connection tests**

Append to `tests/cli/test_connection.py`:

```python
from datetime import UTC, datetime

from evalkit.client.connection import MachineLogin, OidcLogin

LOGIN = OidcLogin(
    issuer="https://idp.test",
    client_id="evalkit-ui",
    token_endpoint="https://idp.test/token",
    access_token="at-1",
    refresh_token="rt-1",
    expires_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
)


def test_save_and_load_an_oidc_login(tmp_path):
    path = tmp_path / "config.toml"
    connection.save(Connection("https://dev", None, "ann", oidc=LOGIN), path=path)
    text = path.read_text()
    assert "[oidc]" in text and 'refresh_token = "rt-1"' in text and "token = " not in text.split("[oidc]")[0]
    assert connection.load(path) == Connection("https://dev", None, "ann", oidc=LOGIN)


def test_active_prefers_client_credentials_from_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    connection.save(Connection("https://dev", "t1", "a"), path=path)
    monkeypatch.setenv("EVALKIT_SERVER_URL", "https://env")
    monkeypatch.setenv("EVALKIT_TOKEN", "envtok")
    monkeypatch.setenv("EVALKIT_OIDC_CLIENT_ID", "ci")
    monkeypatch.setenv("EVALKIT_OIDC_CLIENT_SECRET", "s3cret")
    active = connection.active(path=path)
    assert active == Connection("https://env", None, None, from_env=True, machine=MachineLogin("ci", "s3cret"))
    monkeypatch.delenv("EVALKIT_OIDC_CLIENT_SECRET")
    assert connection.active(path=path) == Connection("https://env", "envtok", None, from_env=True)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/cli/test_connection.py -v`
Expected: FAIL with `ImportError: cannot import name 'MachineLogin'`.

- [ ] **Step 3: Implement the connection model**

In `src/evalkit/client/connection.py`:

```python
class OidcLogin(NamedTuple):
    issuer: str
    client_id: str
    token_endpoint: str
    access_token: str
    refresh_token: str
    expires_at: datetime


class MachineLogin(NamedTuple):
    client_id: str
    client_secret: str


class Connection(NamedTuple):
    url: str
    token: str | None
    username: str | None
    from_env: bool = False
    oidc: OidcLogin | None = None
    machine: MachineLogin | None = None
```

`load`:

```python
def load(path: Path | None = None) -> Connection | None:
    target = path or CONFIG_PATH
    if not target.is_file():
        return None
    data = tomllib.loads(target.read_text())
    if "url" not in data:
        if "profiles" in data:
            raise LookupError(
                f"{target} holds server profiles, which evalkit no longer supports; "
                "run `evalkit login --url <url>` to replace it"
            )
        return None
    oidc = None
    if "oidc" in data:
        raw = data["oidc"]
        oidc = OidcLogin(
            issuer=str(raw["issuer"]),
            client_id=str(raw["client_id"]),
            token_endpoint=str(raw["token_endpoint"]),
            access_token=str(raw["access_token"]),
            refresh_token=str(raw["refresh_token"]),
            expires_at=datetime.fromisoformat(str(raw["expires_at"])),
        )
    return Connection(str(data["url"]), data.get("token"), data.get("username"), oidc=oidc)
```

`save`:

```python
def save(connection: Connection, path: Path | None = None) -> Path:
    target = path or CONFIG_PATH
    lines = [f"url = {_toml_string(connection.url)}"]
    if connection.token is not None:
        lines.append(f"token = {_toml_string(connection.token)}")
    if connection.username is not None:
        lines.append(f"username = {_toml_string(connection.username)}")
    if connection.oidc is not None:
        lines += ["", "[oidc]"]
        lines += [
            f"{field} = {_toml_string(str(value) if field != 'expires_at' else value.isoformat())}"
            for field, value in connection.oidc._asdict().items()
        ]
    ... (the mkstemp / os.replace block unchanged)
```

`active`:

```python
def active(path: Path | None = None) -> Connection:
    env_url = os.environ.get("EVALKIT_SERVER_URL")
    if env_url:
        client_id = os.environ.get("EVALKIT_OIDC_CLIENT_ID")
        client_secret = os.environ.get("EVALKIT_OIDC_CLIENT_SECRET")
        if client_id and client_secret:
            return Connection(
                env_url, None, None, from_env=True, machine=MachineLogin(client_id, client_secret)
            )
        return Connection(env_url, os.environ.get("EVALKIT_TOKEN"), None, from_env=True)
    stored = load(path or CONFIG_PATH)
    if stored is None:
        raise LookupError(
            "no server configured — run `evalkit login --url <url>` or set EVALKIT_SERVER_URL"
        )
    return stored
```

`renew_hint` gains a branch: `if connection.machine is not None: return "check EVALKIT_OIDC_CLIENT_ID and EVALKIT_OIDC_CLIENT_SECRET"`.

- [ ] **Step 4: Run the connection tests**

Run: `uv run pytest tests/cli/test_connection.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing source tests**

Create `tests/client/test_oidc_sources.py`:

```python
import threading
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from evalkit.client import connection
from evalkit.client.connection import Connection, MachineLogin, OidcLogin
from evalkit.client.oidc import (
    ClientCredentials,
    EvalkitAuth,
    LoginExpired,
    StaticToken,
    StoredOidcLogin,
)
from tests.identity import FakeIdp


def stored(idp: FakeIdp, tmp_path, *, seconds_left: int) -> tuple[Connection, object]:
    idp.next_login = ("sub-1", "ann@example.com", "ann")
    tokens = idp._issue(idp._login())
    login = OidcLogin(
        issuer=idp.issuer,
        client_id=idp.client_id,
        token_endpoint=f"{idp.issuer}/token",
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds_left),
    )
    conn = Connection("https://evalkit.test", None, "ann", oidc=login)
    path = tmp_path / "config.toml"
    connection.save(conn, path=path)
    return conn, path


def test_static_token():
    assert StaticToken("abc").bearer() == "abc"


def test_stored_login_returns_the_access_token_while_fresh(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=3600)
    source = StoredOidcLogin(conn, path=path, transport=idp.transport())
    assert source.bearer() == conn.oidc.access_token
    assert connection.load(path).oidc.refresh_token == conn.oidc.refresh_token


def test_stored_login_refreshes_inside_the_last_minute_and_rewrites_the_file(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    source = StoredOidcLogin(conn, path=path, transport=idp.transport())
    token = source.bearer()
    assert token != conn.oidc.access_token
    saved = connection.load(path).oidc
    assert saved.access_token == token and saved.refresh_token != conn.oidc.refresh_token
    assert saved.expires_at > datetime.now(UTC) + timedelta(seconds=3000)
    assert jwt.decode(token, options={"verify_signature": False})["sub"] == "sub-1"


def test_stored_login_adopts_tokens_another_process_saved(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    other = StoredOidcLogin(conn, path=path, transport=idp.transport())
    rotated = other.bearer()
    stale = StoredOidcLogin(conn, path=path, transport=idp.transport())
    assert stale.bearer() == rotated


def test_stored_login_raises_when_nothing_newer_exists(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    idp.refresh_tokens.clear()
    source = StoredOidcLogin(conn, path=path, transport=idp.transport())
    with pytest.raises(LoginExpired, match="run `evalkit login --url https://evalkit.test`"):
        source.bearer()


def test_stored_login_refreshes_once_under_concurrency(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    source = StoredOidcLogin(conn, path=path, transport=idp.transport())
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(source.bearer())) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1


def test_client_credentials_fetches_once_and_keeps_it_in_memory(tmp_path):
    idp = FakeIdp()
    idp.machine_clients["ci"] = ("s3cret", "machine-sub")
    source = ClientCredentials(MachineLogin("ci", "s3cret"), f"{idp.issuer}/token", {}, transport=idp.transport())
    first = source.bearer()
    assert source.bearer() == first
    assert jwt.decode(first, options={"verify_signature": False})["sub"] == "machine-sub"
    source.expires_at = datetime.now(UTC)
    assert source.bearer() != first
    assert not (tmp_path / "config.toml").exists()


def test_evalkit_auth_sets_the_header_per_request():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Authorization"])
        return httpx.Response(200)

    class Counter:
        n = 0

        def bearer(self) -> str:
            self.n += 1
            return f"t{self.n}"

    with httpx.Client(transport=httpx.MockTransport(handler), auth=EvalkitAuth(Counter())) as http:
        http.get("https://x/a")
        http.get("https://x/b")
    assert calls == ["Bearer t1", "Bearer t2"]
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/client/test_oidc_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'evalkit.client.oidc'`.

- [ ] **Step 7: Implement `client/oidc.py` (sources and auth)**

```python
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx
from authlib.integrations.httpx_client import OAuth2Client

from evalkit.client import connection
from evalkit.client.connection import Connection, MachineLogin, OidcLogin

REFRESH_WINDOW_SECONDS = 60


class LoginExpired(Exception):
    def __init__(self, url: str):
        super().__init__(f"your login has expired; run `evalkit login --url {url}`")


class TokenSource(Protocol):
    def bearer(self) -> str: ...


class StaticToken:
    def __init__(self, token: str) -> None:
        self.token = token

    def bearer(self) -> str:
        return self.token


def expires_within(expires_at: datetime, seconds: int) -> bool:
    return expires_at - datetime.now(UTC) < timedelta(seconds=seconds)


def expiry_from(tokens: dict) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"]))


class StoredOidcLogin:
    def __init__(
        self,
        conn: Connection,
        path: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        assert conn.oidc is not None
        self.conn = conn
        self.path = path
        self.transport = transport
        self.lock = threading.Lock()

    def bearer(self) -> str:
        with self.lock:
            login = self.conn.oidc
            assert login is not None
            if not expires_within(login.expires_at, REFRESH_WINDOW_SECONDS):
                return login.access_token
            refreshed = self.refresh(login)
            if refreshed is None:
                refreshed = self.adopt_newer(login)
            if refreshed is None:
                raise LoginExpired(self.conn.url)
            self.conn = self.conn._replace(oidc=refreshed)
            return refreshed.access_token

    def refresh(self, login: OidcLogin) -> OidcLogin | None:
        client = OAuth2Client(login.client_id, transport=self.transport)
        resp = client.post(
            login.token_endpoint,
            data={
                "grant_type": "refresh_token",
                "refresh_token": login.refresh_token,
                "client_id": login.client_id,
            },
            withhold_token=True,
        )
        if resp.status_code != 200:
            return None
        tokens = resp.json()
        refreshed = login._replace(
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", login.refresh_token),
            expires_at=expiry_from(tokens),
        )
        connection.save(self.conn._replace(oidc=refreshed), path=self.path)
        return refreshed

    def adopt_newer(self, login: OidcLogin) -> OidcLogin | None:
        fresh = connection.load(self.path or connection.CONFIG_PATH)
        if fresh is None or fresh.oidc is None or fresh.oidc.access_token == login.access_token:
            return None
        if expires_within(fresh.oidc.expires_at, REFRESH_WINDOW_SECONDS):
            return None
        return fresh.oidc


class ClientCredentials:
    def __init__(
        self,
        machine: MachineLogin,
        token_endpoint: str,
        authorize_params: dict[str, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.machine = machine
        self.token_endpoint = token_endpoint
        self.authorize_params = authorize_params
        self.transport = transport
        self.token: str | None = None
        self.expires_at = datetime.now(UTC)
        self.lock = threading.Lock()

    def bearer(self) -> str:
        with self.lock:
            if self.token is None or expires_within(self.expires_at, REFRESH_WINDOW_SECONDS):
                client = OAuth2Client(
                    self.machine.client_id, self.machine.client_secret, transport=self.transport
                )
                tokens = client.fetch_token(
                    self.token_endpoint, grant_type="client_credentials", **self.authorize_params
                )
                self.token = tokens["access_token"]
                self.expires_at = expiry_from(tokens)
            return self.token


class EvalkitAuth(httpx.Auth):
    def __init__(self, source: TokenSource) -> None:
        self.source = source

    def auth_flow(self, request: httpx.Request):
        request.headers["Authorization"] = f"Bearer {self.source.bearer()}"
        yield request
```

`refresh` posts the form itself rather than calling `client.refresh_token` so a refused refresh is a `None`, not an exception to catch; authlib's `OAuth2Client` is still the HTTP client so the public-client form (`client_id` in the body, no secret) is the standard one. `ClientCredentials` uses `fetch_token`, which raises `OAuthError` on a refused grant; that error reaches the user as the IdP's own message, which is the right outcome for a wrong secret.

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/client/test_oidc_sources.py tests/cli/test_connection.py -v`
Expected: PASS. If `withhold_token=True` is rejected by this authlib version, replace that `client.post` with a plain `httpx.Client(transport=self.transport).post(...)`.

- [ ] **Step 9: Commit**

```bash
uv run ruff check src/evalkit/client tests/client/test_oidc_sources.py tests/cli/test_connection.py && uv run ruff format src/evalkit/client/oidc.py src/evalkit/client/connection.py tests/client/test_oidc_sources.py tests/cli/test_connection.py && uv run mypy src/evalkit
git add src/evalkit/client/oidc.py src/evalkit/client/connection.py tests/client/test_oidc_sources.py tests/cli/test_connection.py
git commit -m "client/oidc: token sources, the httpx auth hook and the [oidc] login on disk

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 13: `build_auth`, `fetch_providers`, and `SyncApi`/`AsyncApi` take `auth`

**Files:**
- Modify: `src/evalkit/client/oidc.py` (`fetch_providers`, `build_auth`)
- Modify: `src/evalkit/client/api.py`
- Modify: `src/evalkit/cli/client.py`, `src/evalkit/cli/main.py`, `src/evalkit/cli/local_run.py`, `src/evalkit/sdk/runner.py`
- Modify: tests that construct `SyncApi`, `AsyncApi` or `ApiClient` with a token: `tests/client/test_api_errors_and_retry.py`, `tests/client/test_http_blob.py`, `tests/client/test_http_store.py`, `tests/client/test_http_sessions.py`, `tests/api/test_traces.py`, `tests/cli/test_update.py`, `tests/cli/test_traces_commands.py`, `tests/cli/test_local_run.py`, `tests/cli/test_version_check.py`
- Test: `tests/client/test_oidc_sources.py`

**Interfaces:**
- Produces: `evalkit.client.oidc.fetch_providers(url: str, transport=None) -> dict` (GET `{url}/api/auth/providers`); `evalkit.client.oidc.build_auth(conn: Connection, *, path=None, transport=None) -> EvalkitAuth | None` (`None` when the connection has no credential at all); `evalkit.client.oidc.static_auth(token: str) -> EvalkitAuth`; `SyncApi(server_url, auth: httpx.Auth | None = None, *, transport=None)`, `AsyncApi(server_url, auth: httpx.Auth | None = None, *, transport=None)`, `ApiClient(server_url, auth=None, *, transport=None, check_version=True)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/client/test_oidc_sources.py`:

```python
from evalkit.client.oidc import build_auth, fetch_providers, static_auth


def providers_transport(idp: FakeIdp) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/providers":
            return httpx.Response(200, json={"password": "admin", "oidc": {"token_endpoint": f"{idp.issuer}/token", "authorize_params": {"resource": idp.audience}}})
        return idp._handle_httpx(request)

    return httpx.MockTransport(handler)


def test_build_auth_picks_the_source(tmp_path):
    idp = FakeIdp()
    idp.machine_clients["ci"] = ("s3cret", "machine-sub")
    assert build_auth(Connection("https://x", None, None)) is None
    assert build_auth(Connection("https://x", "tok", None)).source.bearer() == "tok"
    conn, path = stored(idp, tmp_path, seconds_left=3600)
    assert build_auth(conn, path=path, transport=idp.transport()).source.bearer() == conn.oidc.access_token
    machine = Connection("https://evalkit.test", None, None, from_env=True, machine=MachineLogin("ci", "s3cret"))
    auth = build_auth(machine, transport=providers_transport(idp))
    assert jwt.decode(auth.source.bearer(), options={"verify_signature": False})["sub"] == "machine-sub"


def test_static_auth():
    assert static_auth("t").source.bearer() == "t"
```

Append to `tests/client/test_api_errors_and_retry.py` a test that `SyncApi("http://x", auth=static_auth("tok"), transport=...)` sends `Authorization: Bearer tok` on a request, using the file's existing `MockTransport` helper.

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/client/test_oidc_sources.py tests/client/test_api_errors_and_retry.py -v`
Expected: FAIL on the imports.

- [ ] **Step 3: Add `fetch_providers`, `build_auth`, `static_auth`**

Append to `src/evalkit/client/oidc.py`:

```python
def fetch_providers(url: str, transport: httpx.BaseTransport | None = None) -> dict:
    with httpx.Client(base_url=url.rstrip("/"), transport=transport, timeout=30.0) as http:
        resp = http.get("/api/auth/providers")
        resp.raise_for_status()
        return resp.json()


def static_auth(token: str) -> EvalkitAuth:
    return EvalkitAuth(StaticToken(token))


def build_auth(
    conn: Connection,
    *,
    path: Path | None = None,
    transport: httpx.BaseTransport | None = None,
) -> EvalkitAuth | None:
    if conn.machine is not None:
        oidc = fetch_providers(conn.url, transport)["oidc"]
        if oidc is None:
            raise LookupError(
                "EVALKIT_OIDC_CLIENT_ID is set but the server has no identity provider configured"
            )
        return EvalkitAuth(
            ClientCredentials(
                conn.machine, oidc["token_endpoint"], oidc["authorize_params"], transport
            )
        )
    if conn.oidc is not None:
        return EvalkitAuth(StoredOidcLogin(conn, path=path, transport=transport))
    if conn.token is not None:
        return static_auth(conn.token)
    return None
```

- [ ] **Step 4: Change the API clients**

In `src/evalkit/client/api.py`, both constructors become:

```python
    def __init__(
        self,
        server_url: str,
        auth: httpx.Auth | None = None,
        *,
        transport: ... = None,
    ):
        self._client = httpx.AsyncClient(   # or httpx.Client
            base_url=server_url.rstrip("/"),
            auth=auth,
            timeout=timeout_seconds(),
            transport=...,
        )
```

(delete the `headers={"Authorization": ...}` line in each). In `src/evalkit/cli/client.py`, `ApiClient.__init__(self, server_url, auth: httpx.Auth | None = None, *, transport=None, check_version=True)` and `super().__init__(server_url, auth, transport=transport)`.

- [ ] **Step 5: Update the callers**

```bash
grep -rn "ApiClient(\|SyncApi(\|AsyncApi(" src tests | grep -v "class \|def __init__"
```

For each hit, replace the second positional token argument:

- `src/evalkit/cli/main.py` `_client()`:

```python
def _client() -> ApiClient:
    active = _active_connection()
    try:
        auth = oidc.build_auth(active)
    except LookupError as exc:
        raise ApiError(str(exc)) from exc
    return ApiClient(active.url, auth)
```

with `from evalkit.client import connection, oidc`. The `try`/`except LookupError` mirrors `_active_connection`, which already converts `LookupError` into the CLI's exit-1 error. `ApiClient(url)` in `login` and `ApiClient(server_url, check_version=False)` in `update` stay as they are (no auth).
- `src/evalkit/cli/local_run.py`, five sites: `AsyncApi(conn.url, oidc.build_auth(conn), transport=api_transport)` (and the one without a transport likewise).
- `src/evalkit/sdk/runner.py`, three sites: `AsyncApi(active.url, oidc.build_auth(active), transport=api_transport)` and `SyncApi(active.url, oidc.build_auth(active))`, with `from evalkit.client import connection, oidc`.
- Tests: `SyncApi("http://x", "tok", ...)` → `SyncApi("http://x", static_auth("tok"), ...)`; same for `AsyncApi` and `ApiClient`. Where a test passed `None` as the token, pass nothing.

`LoginExpired` raised inside `auth_flow` surfaces from the HTTP call. In `src/evalkit/cli/main.py` add it to the exceptions the CLI turns into `ApiError` where `ApiRequestError` is handled (search for `except ApiRequestError`), and in `src/evalkit/sdk/runner.py` let it propagate: it is the right error for a run to fail with.

- [ ] **Step 6: Run the client, CLI and SDK suites**

Run: `uv run pytest tests/client tests/cli tests/sdk -v -x`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
uv run ruff check src tests && uv run ruff format src/evalkit/client src/evalkit/cli src/evalkit/sdk tests/client tests/cli tests/api/test_traces.py && uv run mypy src/evalkit
git add src/evalkit/client/oidc.py src/evalkit/client/api.py src/evalkit/cli/client.py src/evalkit/cli/main.py src/evalkit/cli/local_run.py src/evalkit/sdk/runner.py tests/client tests/cli tests/api/test_traces.py
git commit -m "API clients take an httpx.Auth; build_auth picks the token source per connection

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 14: `evalkit login` through the IdP, and `whoami`

**Files:**
- Modify: `src/evalkit/client/oidc.py` (`run_loopback_flow`, `run_device_flow`, `register_identity`, `login_from_tokens`)
- Modify: `src/evalkit/cli/main.py` (`login`, `whoami`)
- Test: `tests/client/test_oidc_flows.py` (new), `tests/cli/test_cli.py`

**Interfaces:**
- Produces: `run_loopback_flow(providers_oidc: dict, open_browser: Callable[[str], None], *, timeout: float = 300, transport=None) -> dict` (the IdP token response); `run_device_flow(providers_oidc: dict, echo: Callable[[str], None], *, sleep: Callable[[float], None] = time.sleep, transport=None) -> dict`; `register_identity(url: str, tokens: dict, transport=None) -> dict` (POSTs `/api/auth/oidc/register`, returns its body); `login_from_tokens(url: str, providers_oidc: dict, tokens: dict) -> Connection` (builds the `Connection` with its `OidcLogin`); `NoDeviceGrant(Exception)`; `LoginTimedOut(Exception)`.

- [ ] **Step 1: Write the failing flow tests**

Create `tests/client/test_oidc_flows.py`:

```python
import threading
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest

from evalkit.client.oidc import (
    LoginTimedOut,
    NoDeviceGrant,
    login_from_tokens,
    run_device_flow,
    run_loopback_flow,
)
from tests.identity import FakeIdp


def providers_for(idp: FakeIdp, *, device: bool = True) -> dict:
    disc = idp.discovery()
    return {
        "name": "Fake",
        "issuer": idp.issuer,
        "client_id": idp.client_id,
        "audience": idp.audience,
        "scopes": ["openid", "profile", "email", "offline_access"],
        "authorize_params": {"resource": idp.audience},
        "authorization_endpoint": disc["authorization_endpoint"],
        "token_endpoint": disc["token_endpoint"],
        "device_authorization_endpoint": disc["device_authorization_endpoint"] if device else None,
    }


def browser_for(idp: FakeIdp):
    def open_browser(url: str) -> None:
        def follow() -> None:
            with httpx.Client(transport=idp.transport()) as at_idp:
                redirect = at_idp.get(url)
            assert redirect.status_code == 302, redirect.text
            httpx.get(redirect.headers["location"])

        threading.Thread(target=follow, daemon=True).start()

    return open_browser


def test_loopback_flow_sends_pkce_and_the_authorize_params(tmp_path):
    idp = FakeIdp()
    idp.next_login = ("sub-1", "ann@example.com", "ann")
    seen: list[str] = []

    def spy(url: str) -> None:
        seen.append(url)
        browser_for(idp)(url)

    tokens = run_loopback_flow(providers_for(idp), spy, transport=idp.transport())
    query = parse_qs(urlsplit(seen[0]).query)
    assert query["code_challenge_method"] == ["S256"] and query["resource"] == [idp.audience]
    assert query["redirect_uri"][0].startswith("http://127.0.0.1:")
    assert jwt.decode(tokens["id_token"], options={"verify_signature": False})["sub"] == "sub-1"
    assert "refresh_token" in tokens


def test_loopback_flow_times_out():
    idp = FakeIdp()
    with pytest.raises(LoginTimedOut):
        run_loopback_flow(providers_for(idp), lambda url: None, timeout=0.2, transport=idp.transport())


def test_device_flow_polls_until_the_person_finishes():
    idp = FakeIdp()
    idp.next_login = ("sub-2", "bob@example.com", None)
    idp.device_pending = 2
    lines: list[str] = []
    sleeps: list[float] = []
    tokens = run_device_flow(providers_for(idp), lines.append, sleep=sleeps.append, transport=idp.transport())
    assert lines == [f"open {idp.issuer}/device/verify and enter ABCD-EFGH"]
    assert len(sleeps) == 2
    assert jwt.decode(tokens["id_token"], options={"verify_signature": False})["sub"] == "sub-2"


def test_device_flow_needs_the_endpoint():
    idp = FakeIdp()
    with pytest.raises(NoDeviceGrant):
        run_device_flow(providers_for(idp, device=False), print, transport=idp.transport())


def test_login_from_tokens_builds_the_connection():
    idp = FakeIdp()
    idp.next_login = ("sub-1", "ann@example.com", "ann")
    tokens = idp._issue(idp._login())
    conn = login_from_tokens("https://evalkit.test", providers_for(idp), tokens)
    assert conn.oidc.refresh_token == tokens["refresh_token"] and conn.oidc.token_endpoint == f"{idp.issuer}/token"
    assert conn.token is None
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/client/test_oidc_flows.py -v`
Expected: FAIL on the imports.

- [ ] **Step 3: Implement the flows**

Append to `src/evalkit/client/oidc.py`:

```python
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit

from authlib.integrations.base_client.errors import OAuthError
from authlib.oauth2.rfc8628 import DEVICE_CODE_GRANT_TYPE

LOOPBACK_TIMEOUT_SECONDS = 300.0
LOOPBACK_PAGE = b"<!doctype html><title>evalkit</title><p>Logged in. You can close this tab."


class LoginTimedOut(Exception):
    def __init__(self) -> None:
        super().__init__("login timed out; run `evalkit login` again")


class NoDeviceGrant(Exception):
    def __init__(self) -> None:
        super().__init__(
            "this identity provider has no device authorization endpoint; "
            "log in from a machine with a browser"
        )


def run_loopback_flow(
    providers_oidc: dict,
    open_browser: Callable[[str], None],
    *,
    timeout: float = LOOPBACK_TIMEOUT_SECONDS,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    received: dict[str, str] = {}
    done = threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received.update({k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(LOOPBACK_PAGE)
            done.set()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Callback)
    port = server.server_address[1]
    client = OAuth2Client(
        providers_oidc["client_id"],
        scope=" ".join(providers_oidc["scopes"]),
        redirect_uri=f"http://127.0.0.1:{port}/callback",
        code_challenge_method="S256",
        transport=transport,
    )
    verifier = generate_verifier()
    url, state = client.create_authorization_url(
        providers_oidc["authorization_endpoint"],
        code_verifier=verifier,
        **providers_oidc["authorize_params"],
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    open_browser(url)
    finished = done.wait(timeout)
    server.shutdown()
    if not finished:
        raise LoginTimedOut()
    if received.get("state") != state:
        raise OAuthError(error="invalid_state", description="state mismatch on the callback")
    return client.fetch_token(
        providers_oidc["token_endpoint"], code=received["code"], code_verifier=verifier
    )


def generate_verifier() -> str:
    return secrets.token_urlsafe(48)


def run_device_flow(
    providers_oidc: dict,
    echo: Callable[[str], None],
    *,
    sleep: Callable[[float], None] = time.sleep,
    transport: httpx.BaseTransport | None = None,
) -> dict:
    endpoint = providers_oidc["device_authorization_endpoint"]
    if endpoint is None:
        raise NoDeviceGrant()
    client = OAuth2Client(providers_oidc["client_id"], transport=transport)
    started = client.post(
        endpoint,
        data={
            "client_id": providers_oidc["client_id"],
            "scope": " ".join(providers_oidc["scopes"]),
            **providers_oidc["authorize_params"],
        },
        withhold_token=True,
    ).json()
    echo(f"open {started['verification_uri']} and enter {started['user_code']}")
    interval = float(started.get("interval", 5))
    deadline = time.monotonic() + float(started.get("expires_in", 300))
    while time.monotonic() < deadline:
        try:
            return client.fetch_token(
                providers_oidc["token_endpoint"],
                grant_type=DEVICE_CODE_GRANT_TYPE,
                device_code=started["device_code"],
            )
        except OAuthError as exc:
            if exc.error == "slow_down":
                interval += 5
            elif exc.error != "authorization_pending":
                raise
        sleep(interval)
    raise LoginTimedOut()


def register_identity(url: str, tokens: dict, transport: httpx.BaseTransport | None = None) -> dict:
    with httpx.Client(base_url=url.rstrip("/"), transport=transport, timeout=30.0) as http:
        resp = http.post(
            "/api/auth/oidc/register",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            json={"id_token": tokens["id_token"]},
        )
        if resp.status_code >= 400:
            raise LookupError(response_detail(resp))
        return resp.json()


def login_from_tokens(url: str, providers_oidc: dict, tokens: dict) -> Connection:
    return Connection(
        url,
        None,
        None,
        oidc=OidcLogin(
            issuer=providers_oidc["issuer"],
            client_id=providers_oidc["client_id"],
            token_endpoint=providers_oidc["token_endpoint"],
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=expiry_from(tokens),
        ),
    )
```

Add `import secrets` and `from evalkit.client.api import response_detail` to the module's imports. The `try`/`except OAuthError` in the device loop is the path the spec names (`authorization_pending`, `slow_down`). `register_identity` raises `LookupError` with the server's detail so the CLI prints the 409's message (which names the subject to bind) through its usual `ApiError` conversion.

- [ ] **Step 4: Run the flow tests**

Run: `uv run pytest tests/client/test_oidc_flows.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing CLI tests**

Append to `tests/cli/test_cli.py`:

```python
from evalkit.client import oidc as client_oidc


def test_login_falls_back_to_the_password_path_without_an_idp(server, tmp_path, monkeypatch):
    base_url, _ = server
    config_path = tmp_path / "login.toml"
    monkeypatch.setattr(connection, "CONFIG_PATH", config_path)
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", base_url], input="admin\nhunter2-pw\n")
    assert result.exit_code == 0, result.output
    assert "token = " in config_path.read_text()


def test_login_runs_the_loopback_flow_and_registers(tmp_path, monkeypatch):
    from tests.identity import FakeIdp

    idp = FakeIdp()
    idp.next_login = ("sub-ann", "ann@example.com", "ann")
    providers = {"password": "admin", "oidc": {**idp.discovery(), "name": "Fake", "client_id": idp.client_id, "audience": idp.audience, "scopes": ["openid", "email", "profile", "offline_access"], "authorize_params": {}}}
    monkeypatch.setattr(client_oidc, "fetch_providers", lambda url, transport=None: providers)
    monkeypatch.setattr(client_oidc, "register_identity", lambda url, tokens, transport=None: {"username": "ann", "is_admin": False, "email": "ann@example.com", "kind": "human", "created": True})
    monkeypatch.setattr(client_oidc, "run_loopback_flow", lambda oidc_cfg, open_browser, **kw: idp._issue(idp._login()))
    config_path = tmp_path / "login.toml"
    monkeypatch.setattr(connection, "CONFIG_PATH", config_path)
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", "https://evalkit.test"])
    assert result.exit_code == 0, result.output
    assert "logged in as ann on https://evalkit.test" in result.output
    saved = connection.load(config_path)
    assert saved.username == "ann" and saved.oidc is not None and saved.token is None


def test_login_no_browser_uses_the_device_flow(tmp_path, monkeypatch):
    from tests.identity import FakeIdp

    idp = FakeIdp()
    idp.next_login = ("sub-ann", "ann@example.com", "ann")
    providers = {"password": "admin", "oidc": {**idp.discovery(), "name": "Fake", "client_id": idp.client_id, "audience": idp.audience, "scopes": ["openid"], "authorize_params": {}}}
    called: list[str] = []
    monkeypatch.setattr(client_oidc, "fetch_providers", lambda url, transport=None: providers)
    monkeypatch.setattr(client_oidc, "register_identity", lambda url, tokens, transport=None: {"username": "ann", "is_admin": False, "email": "ann@example.com", "kind": "human", "created": False})
    monkeypatch.setattr(client_oidc, "run_device_flow", lambda oidc_cfg, echo, **kw: (called.append("device"), idp._issue(idp._login()))[1])
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "login.toml")
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", "https://evalkit.test", "--no-browser"])
    assert result.exit_code == 0, result.output and called == ["device"]


def test_whoami_names_the_login_kind(env, monkeypatch):
    result = runner.invoke(cli_app, ["whoami"])
    assert result.exit_code == 0 and "login: password" in result.output
```

- [ ] **Step 6: Run them to verify they fail**

Run: `uv run pytest tests/cli/test_cli.py -k "login or whoami" -v`
Expected: the loopback, device and `whoami` tests FAIL (unknown option `--no-browser`; no `login:` line).

- [ ] **Step 7: Implement `login` and `whoami`**

In `src/evalkit/cli/main.py` replace `login`:

```python
@app.command()
def login(
    url: str = typer.Option(None, "--url", help="Server base URL, e.g. https://evalkit.internal"),
    username: str = typer.Option(None, "--username", help="Password login; with SSO configured, admins only unless password_login is all."),
    password: str = typer.Option(None, "--password"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Headless machine: show a code to enter on another device (device authorization grant)."),
):
    """Log in to a server and store the login in ~/.evalkit/config.toml, replacing any
    earlier one.

    With single sign-on configured on the server, the command opens your browser at
    the identity provider and stores the IdP's tokens; they refresh themselves. Pass
    --username for a password login instead. Example: evalkit login --url https://evalkit.example.com
    """
    url = url or typer.prompt("Server URL")
    providers = oidc.fetch_providers(url)
    if providers["oidc"] is None or username is not None:
        username = username or typer.prompt("Username")
        password = password or typer.prompt("Password", hide_input=True)
        body = (
            ApiClient(url)
            .post("/api/auth/login", json={"username": username, "password": password})
            .json()
        )
        path = connection.save(connection.Connection(url, body["token"], body["username"]))
        typer.echo(f"logged in as {username} on {url}; saved to {path}")
        return
    try:
        if no_browser:
            tokens = oidc.run_device_flow(providers["oidc"], typer.echo)
        else:
            typer.echo("opening your browser to sign in; if it does not open, use the URL below")
            tokens = oidc.run_loopback_flow(providers["oidc"], _open_browser)
        me = oidc.register_identity(url, tokens)
    except (oidc.LoginTimedOut, oidc.NoDeviceGrant, LookupError) as exc:
        raise ApiError(str(exc)) from exc
    path = connection.save(oidc.login_from_tokens(url, providers["oidc"], tokens)._replace(username=me["username"]))
    typer.echo(f"logged in as {me['username']} on {url}; saved to {path}")


def _open_browser(url: str) -> None:
    typer.echo(url)
    webbrowser.open(url)
```

with `import webbrowser`. The `try`/`except` turns the three named failures into the CLI's red one-line exit, the same as every other `ApiError`. In `whoami`, after the `username on url` line:

```python
    if active.machine is not None:
        kind = "client credentials"
    elif active.oidc is not None:
        kind = "sso (refresh token stored; the access token renews itself)"
    else:
        kind = "password"
    typer.echo(f"login: {kind}")
```

- [ ] **Step 8: Run the tests**

Run: `uv run pytest tests/cli/test_cli.py -k "login or whoami" tests/client/test_oidc_flows.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
uv run ruff check src/evalkit/client/oidc.py src/evalkit/cli/main.py tests && uv run ruff format src/evalkit/client/oidc.py src/evalkit/cli/main.py tests/client/test_oidc_flows.py tests/cli/test_cli.py && uv run mypy src/evalkit
git add src/evalkit/client/oidc.py src/evalkit/cli/main.py tests/client/test_oidc_flows.py tests/cli/test_cli.py
git commit -m "evalkit login signs in through the identity provider; --no-browser uses the device grant

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 15: Trace export requires `EVALKIT_OTEL_TOKEN`

**Files:**
- Modify: `src/evalkit/sdk/tracing.py`
- Test: `tests/sdk/test_tracing.py`

**Interfaces:**
- Produces: `resolve_destination() -> tuple[str, str]` raising `evalkit.credentials.MissingCredential` when `EVALKIT_OTEL_TOKEN` is unset; `headers(agent)` always carries `Authorization`.

- [ ] **Step 1: Change the tests**

In `tests/sdk/test_tracing.py`:

- `test_collector_url_and_token_resolution`: keep as is.
- `test_register_adds_an_exporter_to_the_global_provider`: replace `monkeypatch.setenv("EVALKIT_TOKEN", "user-token")` and the `delenv` with `monkeypatch.setenv("EVALKIT_OTEL_TOKEN", "agent-token")`, and assert `request["authorization"] == "Bearer agent-token"`.
- `test_exporter_prefers_the_agent_token_from_the_environment`: rename to `test_exporter_uses_the_agent_token`, delete the `EVALKIT_TOKEN` line.
- Add:

```python
from evalkit.credentials import MissingCredential


def test_export_without_an_agent_token_is_refused(monkeypatch):
    monkeypatch.setenv("EVALKIT_SERVER_URL", "http://env")
    monkeypatch.setenv("EVALKIT_TOKEN", "user-token")
    monkeypatch.delenv("EVALKIT_OTEL_TOKEN", raising=False)
    with pytest.raises(MissingCredential, match="EVALKIT_OTEL_TOKEN"):
        tracing.resolve_destination()
    with pytest.raises(MissingCredential):
        tracing.register(agent="sdk")
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/sdk/test_tracing.py -v`
Expected: the new test FAILS (no exception), the renamed one passes.

- [ ] **Step 3: Implement**

In `src/evalkit/sdk/tracing.py`:

```python
from evalkit.credentials import MissingCredential

OTEL_TOKEN_HINT = (
    "set EVALKIT_OTEL_TOKEN to an agent token (evalkit tokens create --agent <agent>) "
    "before exporting traces; a login token is not accepted here"
)


def resolve_destination() -> tuple[str, str]:
    token = os.environ.get(OTEL_TOKEN_ENV)
    if not token:
        raise MissingCredential(OTEL_TOKEN_HINT)
    url = os.environ.get(SERVER_URL_ENV) or connection.active().url
    return url, token


def headers(agent: str) -> dict[str, str]:
    _, token = resolve_destination()
    return {AGENT_HEADER: agent, "Authorization": f"Bearer {token}"}
```

Check `MissingCredential`'s constructor in `src/evalkit/credentials.py` and call it the way it is defined. Update the `register` docstring (published SDK reference):

```
    Adds an exporter to the global tracer provider, or creates one when none is set.
    Call it after your own tracing setup, so it joins your provider. Spans go to the
    server you logged in to (or `EVALKIT_SERVER_URL`) with the agent token in
    `EVALKIT_OTEL_TOKEN` (`evalkit tokens create --agent <agent>`); a person's login
    is not accepted, because it expires under a long-running process. Needs the
    `otel` extra.
```

- [ ] **Step 4: Run the tests, commit**

Run: `uv run pytest tests/sdk -v`
Expected: PASS.

```bash
uv run ruff format src/evalkit/sdk/tracing.py tests/sdk/test_tracing.py && uv run ruff check src/evalkit/sdk tests/sdk && uv run mypy src/evalkit
git add src/evalkit/sdk/tracing.py tests/sdk/test_tracing.py
git commit -m "tracing.register requires EVALKIT_OTEL_TOKEN; the login-token fallback is removed

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 16: Fake IdP in the e2e services and the e2e flow

**Files:**
- Modify: `tests/e2e/services.py`
- Create: `tests/e2e/README.md` (the manual flow, since e2e is not collected by pytest)

**Interfaces:**
- Produces: the fake IdP served on `127.0.0.1:19100` next to the harness, evaluator and judge, with `next_login` fixed to `("e2e-sub", "e2e@example.com", "e2e")` and the machine client `ci` / `e2e-secret` with subject `e2e-ci`.

- [ ] **Step 1: Serve the IdP**

In `tests/e2e/services.py` add `from tests.identity import FakeIdp` and, in `__main__`:

```python
    idp = FakeIdp(issuer="http://127.0.0.1:19100", audience="http://localhost:8020")
    idp.next_login = ("e2e-sub", "e2e@example.com", "e2e")
    idp.machine_clients["ci"] = ("e2e-secret", "e2e-ci")
    threads.append(threading.Thread(target=serve, args=(idp.asgi(), 19100), daemon=True))
```

and extend the printed line with `identity provider :19100`. The `audience` is the server's `base_url` in the e2e `config.yaml`; change it there if the e2e server runs elsewhere.

- [ ] **Step 2: Write the e2e flow**

Create `tests/e2e/README.md`:

```markdown
# End-to-end SSO flow

Against a built image (see the deployment skill's try-it stack) with this in
the server `config.yaml`:

    auth:
      oidc:
        issuer: http://127.0.0.1:19100
        client_id: evalkit-ui
        audience: http://localhost:8020
        name: Fake
      password_login: admin

1. `uv run python tests/e2e/services.py` in one terminal.
2. `evalkit login --url http://localhost:8020`: the fake authorize endpoint
   redirects straight back, no browser interaction. Expect
   `logged in as e2e on http://localhost:8020`. `evalkit whoami` prints
   `login: sso (...)`.
3. As admin (`evalkit login --url http://localhost:8020 --username admin
   --password ...` in a second config: set `EVALKIT_SERVER_URL` +
   `EVALKIT_TOKEN` from a password login instead):
   `evalkit workspaces create dev`, `evalkit workspaces members add dev e2e
   --role writer`, `evalkit users add ci-bot --subject e2e-ci`,
   `evalkit workspaces members add dev ci-bot --role writer`.
4. As e2e: `evalkit sync skills/evalkit-deployment/try/cases/<suite>
   --workspace dev` succeeds.
5. Machine: `EVALKIT_SERVER_URL=http://localhost:8020
   EVALKIT_OIDC_CLIENT_ID=ci EVALKIT_OIDC_CLIENT_SECRET=e2e-secret evalkit
   whoami` prints `ci-bot` and `login: client credentials`; the same
   environment runs `evalkit run <suite>@dev --agent <agent>@dev`.
6. `evalkit users archive e2e`; the next e2e command fails with 401
   `user is archived`.
```

- [ ] **Step 3: Run the flow once against the try-it stack and record the result in the PR description**

```bash
uv run python tests/e2e/services.py
```

and the steps above. Fix anything the flow reveals before committing.

- [ ] **Step 4: Commit**

```bash
uv run ruff format tests/e2e/services.py && uv run ruff check tests/e2e
git add tests/e2e/services.py tests/e2e/README.md
git commit -m "e2e: fake identity provider on :19100 and the SSO flow

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 17: Docs, skills, changelog, contracts

**Files:**
- Create: `docs/user/sso.md`
- Modify: `docs/user/login.md`, `docs/user/ci-sync.md`, `docs/user/mcp.md`, `docs/user/tracing.md`, `docs/user/quickstart.md`, `docs/user/sdk-runs.md`, `docs/user/concepts.md`, `mkdocs.yml`
- Modify: `skills/evalkit-usage/SKILL.md`, `skills/evalkit-deployment/SKILL.md`, `skills/evalkit-deployment/references/config-and-deploy.md`, `skills/connect-traces/SKILL.md`
- Modify: `AGENTS.md`, `CHANGELOG.md`
- Test: `uv run pytest tests/test_docs.py tests/test_skills.py tests/contracts`, `uv run --group docs mkdocs build --strict`

**Interfaces:**
- Consumes: everything above. The server-side config block is documented in `sso.md` and the deployment skill's reference, not in `docs/user/config.md`, which is the page for the client-side `evalkit.yaml`.

- [ ] **Step 1: Write `docs/user/sso.md`**

```markdown
# Single sign-on

Evalkit signs people in through your identity provider (IdP) with OpenID
Connect. The web UI and the CLI are public OAuth clients: each sends you to
the IdP, receives an access token, and sends that token to evalkit with every
request. Evalkit verifies it with the IdP's published keys and never issues a
login token of its own for SSO users. CI pipelines use a machine client at the
IdP. This page is for the IdP admin who registers evalkit and the evalkit admin
who turns SSO on.

## What evalkit is to the IdP

| Piece | At the IdP | Standard |
| --- | --- | --- |
| The web UI and the CLI | One public client (no secret) | Authorization code with PKCE, RFC 6749 §4.1 and RFC 7636; refresh, RFC 6749 §6 |
| The CLI on a machine without a browser | The same client | Device authorization grant, RFC 8628 |
| Evalkit's API | An API or resource that access tokens name in `aud` | JWT access tokens, RFC 9068 |
| A CI pipeline | One machine client per pipeline, with a secret | Client credentials, RFC 6749 §4.4 |
| Discovery | `<issuer>/.well-known/openid-configuration` | OpenID Connect Discovery 1.0 |

## Requirements

Check each before registering. Evalkit does not work around any of them.

| Requirement | How to check |
| --- | --- |
| OIDC discovery at the issuer | `curl <issuer>/.well-known/openid-configuration` returns `jwks_uri`, `authorization_endpoint`, `token_endpoint` |
| JWT access tokens, not opaque strings, that carry evalkit's audience in `aud` | Decode an access token issued for evalkit (`jwt.io` or `evalkit whoami` after a sign-in); it has three dot-separated parts and `aud` contains the value you configure as `auth.oidc.audience`. Google and Okta's default authorization server issue opaque tokens and cannot be used; Okta needs a custom authorization server |
| `sub`, `exp`, `iat` in every access token | Same decoded token |
| `email` and, when the IdP has one, `preferred_username` in the ID token | Grant the `email` and `profile` scopes to the client; evalkit refuses a first sign-in whose ID token has no `email` |
| PKCE with S256 for the public client | A setting on the client registration |
| Refresh tokens for the public client, with rotation on | Required, not recommended: the web UI keeps its refresh token in browser storage and rotation is what bounds the damage of a stolen one |
| The client credentials grant for machine clients | A setting on each machine client |
| The device authorization grant, optional | Needed only for `evalkit login --no-browser` |

## Registering the public client

One registration, shared by the UI and the CLI. Application type: public,
single-page or native, no client secret. Redirect URIs:

- `https://<evalkit>/login/callback` for the web UI.
- `http://127.0.0.1/callback` for the CLI, with any port (RFC 8252 §7.3; Okta
  and Entra accept the loopback with a wildcard port, Keycloak accepts
  `http://127.0.0.1:*/callback`).

Scopes to allow: `openid`, `profile`, `email`, `offline_access` (or the IdP's
name for refresh tokens). PKCE S256 on. Refresh token rotation on. Copy the
client id into `auth.oidc.client_id`.

## Registering the audience

Access tokens must name evalkit. How that is set up depends on the IdP:

| IdP | How | What goes in config |
| --- | --- | --- |
| Entra ID | Expose an API on the app registration with a scope such as `api://<app id>/evalkit`; grant the public client that scope | `audience: api://<app id>`; add the scope to `scopes` |
| Okta (custom authorization server) | Set the server's audience to evalkit's URL | `audience: https://evalkit.example.com`; `authorize_params: {resource: https://evalkit.example.com}` if the server needs a resource indicator |
| Auth0 | Create an API with evalkit's URL as identifier | `audience: https://evalkit.example.com`; `authorize_params: {audience: https://evalkit.example.com}` |
| Keycloak | Add an audience mapper for the client | `audience: <the mapped value>` |
| Any IdP that follows RFC 8707 | Resource indicators | `authorize_params: {resource: <audience>}` |

Evalkit sends `authorize_params` verbatim on the authorize request and the
device authorization request; the server never interprets them.

## Registering a machine client for CI

One per pipeline. Application type: machine-to-machine or service, client
credentials grant on, with the same audience attached. Note the client id
and secret: they go into the pipeline's secrets as `EVALKIT_OIDC_CLIENT_ID`
and `EVALKIT_OIDC_CLIENT_SECRET`. Note the subject the IdP puts in `sub` for
this client's tokens (Okta and Auth0 derive it from the client id; Entra uses
the service principal's object id; Keycloak the id of the client's service
account user): an evalkit admin binds it with `evalkit users add ci-bot
--subject <sub>`. See [CI sync](ci-sync.md).

## The evalkit side

In the server's `config.yaml`:

    auth:
      token_ttl_hours: 720          # password login tokens
      password_login: admin         # admin (default) or all; ignored without oidc
      oidc:
        issuer: https://id.example.com
        client_id: 0oa1b2c3d4e5f6g7h8
        audience: https://evalkit.example.com
        name: Okta                  # the login button's label
        scopes: [openid, profile, email, offline_access]
        authorize_params: {}
        algorithms: [RS256]

`password_login: admin` keeps password login for admins only, so the
bootstrap `admin` account stays a break-glass login while everyone else uses
SSO. `all` runs both side by side. Restart the server and check
`GET /api/auth/providers`: it lists the IdP's name and endpoints, or answers
503 while the IdP is unreachable (the server still starts, and admin password
login keeps working).

`mcp.oauth` is gone: `/mcp` verifies tokens with the same settings as `/api`,
and an IdP token is an evalkit user there too.

## Admin runbook

1. Start with `EVALKIT_ADMIN_PASSWORD` set, as before; `admin` is a password
   account.
2. Ask the first person to sign in; evalkit creates their user on the first
   sign-in. Then `evalkit users promote <username>`.
3. Bind machine users: `evalkit users add ci-bot --subject <sub>`, then
   `evalkit workspaces members add dev ci-bot --role writer`.
4. A sign-in that answers 409 `username <x> is taken by a user or workspace`
   means the derived username (the part before `@` in `preferred_username`,
   else in `email`) already exists. Link the identity to the account you
   choose: `evalkit users bind <username> --subject <sub>` (the 409 names the
   subject). A password account that predates SSO is linked the same way.
5. Revoke: `evalkit users archive <username>` blocks the next request. The
   IdP's own revocation takes effect within one access token lifetime.

## Troubleshooting

| Answer | Meaning | What to change |
| --- | --- | --- |
| 401 `not registered: subject <sub> has no user` | No user for that identity | A person signs in through the UI or `evalkit login`; a machine's admin runs `users add --subject <sub>` |
| 401 `invalid access token: Audience doesn't match` | The token was not issued for evalkit | Fix the audience registration; check `authorize_params` or the scope |
| 401 `invalid access token: Invalid issuer` | `auth.oidc.issuer` differs from the token's `iss` | Copy the issuer from the discovery document, without a trailing slash |
| 401 `invalid access token: Signature has expired` | Expired access token | The clients refresh automatically; for the CLI, `evalkit login` again if the refresh token is gone |
| 401 `invalid access token: unknown signing key <kid>` | The IdP rotated keys and the token's key is not in the JWKS | The server refetches once a minute; if it persists, the token comes from another issuer |
| 401 `SSO is not configured on this server` | A JWT reached a server without `auth.oidc` | Configure `auth.oidc` or use password login |
| 401 `user is archived` | Archived in evalkit | `evalkit users restore <username>` |
| 403 `password login is for admins on this server` | `password_login: admin` | Sign in with SSO, or set `password_login: all` |
| 503 `identity provider unreachable` | Discovery or the JWKS could not be fetched | Check the issuer URL and network egress from the server; it retries every 30 s |
| No "Continue with ..." button on the login page | `/api/auth/providers` has no `oidc` | `auth.oidc` is absent or the server was not restarted |
| `your login has expired; run evalkit login` | The refresh token was refused and nothing newer was on disk | `evalkit login --url <url>` again |
```

- [ ] **Step 2: Rewrite `docs/user/login.md`**

Replace the page body with:

````markdown
# Login

```bash
evalkit login --url {{origin}}
```

With single sign-on configured on the server, the command opens your browser
at your identity provider. Sign in there; the browser comes back to the CLI
on a local port, and the CLI stores the identity provider's tokens in
`~/.evalkit/config.toml`. They refresh themselves. On a machine without a
browser, `evalkit login --url {{origin}} --no-browser` prints a URL and a code
to enter on any device.

Without single sign-on, or with `--username`, the command asks for a username
and password and stores an evalkit token instead. With single sign-on
configured, password login is for admins unless the deployer set
`password_login: all`.

The CLI talks to one server at a time: logging in again, to the same server
or another, replaces the stored login. Dev and prod are workspaces on one
server (`finance@dev`, `finance@prod`), not separate logins.

`evalkit whoami` shows who the login is, its kind (`sso`, `password` or
`client credentials`) and when the current token expires, and exits 1 if the
server rejects it. An SSO access token expires within the hour and renews
itself; that line is not a reason to log in again. When a command fails with
`your login has expired`, run `evalkit login` again.

The first sign-in creates your evalkit user, named after the part before `@`
in your identity provider's username or email. If that name is taken, the
command says so and names the subject an admin binds with
`evalkit users bind`; see [Single sign-on](sso.md).

## Scripts and CI

Pipelines authenticate as a machine client at the identity provider:

```bash
export EVALKIT_SERVER_URL={{origin}}
export EVALKIT_OIDC_CLIENT_ID=<client id>
export EVALKIT_OIDC_CLIENT_SECRET=<client secret>
```

No `evalkit login` is needed; every command fetches a token itself. An admin
binds the client once: see [CI sync](ci-sync.md). Without single sign-on,
`EVALKIT_SERVER_URL` and `EVALKIT_TOKEN` carry an evalkit token, or the job
logs in with a dedicated non-admin password user.

## Timeout

(unchanged section)

The SDK uses the same login and the same variables. A deployed agent that
only exports traces uses an agent token; see [Tracing](tracing.md).
````

Keep the existing "Timeout" section text verbatim.

- [ ] **Step 3: Update `docs/user/ci-sync.md`**

In "What you need", replace the admin-password bullet with:

```markdown
- Credentials for a machine user. With single sign-on: a machine client at
  the identity provider (see [Single sign-on](sso.md)), its id and secret as
  CI secrets `EVALKIT_OIDC_CLIENT_ID` and `EVALKIT_OIDC_CLIENT_SECRET`, and
  an admin runs `evalkit users add ci-bot --subject <sub>` once and grants it
  writer on the workspace: `evalkit workspaces members add dev ci-bot --role
  writer`. Without single sign-on: a dedicated user with a password, created
  with `evalkit users add ci-bot` and granted writer the same way, with the
  password as a CI secret. Never the admin account.
```

Replace the "Logs in as `admin`" step with "Authenticates as the machine user", and in the GitHub Actions example replace the `Log in` step with:

```yaml
    env:
      EVALKIT_OIDC_CLIENT_ID: ${{ secrets.EVALKIT_OIDC_CLIENT_ID }}
      EVALKIT_OIDC_CLIENT_SECRET: ${{ secrets.EVALKIT_OIDC_CLIENT_SECRET }}
```

on the job, and no login step. Note under it: "Without single sign-on, keep a
`Log in` step: `evalkit login --url "$EVALKIT_SERVER_URL" --username ci-bot
--password "$EVALKIT_CI_PASSWORD"`." `--actor` stays admin-only; say that a
machine user records itself as the actor, and that `--actor` needs an admin
machine user, which `users promote` refuses, so the case changelog shows
`ci-bot` for a sync from CI.

- [ ] **Step 4: Update `docs/user/mcp.md`**

Replace the paragraph starting "When the server's config has an `mcp.oauth` block" and the YAML block and the "An OAuth token is not an evalkit user" paragraph with:

```markdown
With single sign-on configured (`auth.oidc`, see [Single sign-on](sso.md)),
MCP clients that speak OAuth (Claude, Cursor, Codex) connect with the URL
alone: the server points them to the identity provider, and a token issued
for `{{origin}}/mcp` or for evalkit's configured audience is accepted. The
token's subject must be an evalkit user, so archiving someone cuts off their
MCP access too. Evalkit login tokens keep working next to it.
```

- [ ] **Step 5: Update tracing, quickstart, sdk-runs, concepts**

- `docs/user/tracing.md` line 19: replace "and the token from `EVALKIT_OTEL_TOKEN` (an agent token), then `EVALKIT_TOKEN`, then your `evalkit login`" with "and the token from `EVALKIT_OTEL_TOKEN`, an agent token (`evalkit tokens create --agent <agent>`); a login token is not accepted, because it expires under a long-running process". Line 94: delete "`tracing.register`, `tracing.exporter` and `tracing.processor` pick both up" and say they require it.
- `docs/user/quickstart.md`: before the `tracing.register(agent="myagent")` snippet add a step: "Mint an agent token and export it: `evalkit tokens create --agent myagent`, then `export EVALKIT_OTEL_TOKEN=<token>`."
- `docs/user/sdk-runs.md` line 11 and 99: same wording as tracing.md.
- `docs/user/concepts.md`: add rows

```markdown
| Single sign-on             | Signing in through your organisation's identity provider. The first sign-in creates your evalkit user. See [Single sign-on](sso.md). |
| Subject                    | The stable id your identity provider assigns you; evalkit keys your user on it, not on your email. |
| Machine user               | A user bound to a machine client at the identity provider, for CI. No password, no browser. `evalkit users add <name> --subject <sub>`. |
```

- `mkdocs.yml`: add `- Single sign-on: sso.md` under "Get started" after `Login: login.md`.

- [ ] **Step 6: Skills**

- `skills/evalkit-usage/SKILL.md`: in the login item (line 52 area) add the SSO sentence from login.md's first paragraph and `--no-browser`. Replace the CI item (lines 60-76) with: machine client credentials in `EVALKIT_OIDC_CLIENT_ID` / `EVALKIT_OIDC_CLIENT_SECRET` and a bound machine user, or a dedicated non-admin password user without SSO; never the admin account; link `../evalkit-docs/ci-sync.md` and `../evalkit-docs/sso.md`. In the tracing items (lines 202-207) state that `EVALKIT_OTEL_TOKEN` is required.
- `skills/evalkit-deployment/SKILL.md`: in the checklist item 2 and the admin bootstrap paragraph (lines 40, 62-65) add "to turn on single sign-on, set `auth.oidc` (the IdP registration and runbook are on `../evalkit-docs/sso.md`)". Replace the CI paragraph (lines 300-306) with the machine-user setup, linking `../evalkit-docs/ci-sync.md`. Add to the upgrade notes: "A config with `mcp.oauth` fails to load; move the issuer to `auth.oidc`."
- `skills/evalkit-deployment/references/config-and-deploy.md` line 145: replace the `mcp` row with an `auth` row listing `token_ttl_hours`, `password_login`, and `oidc` (`issuer`, `client_id`, `audience`, `name`, `scopes`, `authorize_params`, `algorithms`), linking `../../evalkit-docs/sso.md`.
- `skills/connect-traces/SKILL.md` lines 104 and 115-117: `EVALKIT_OTEL_TOKEN` is required; drop "and token like `register` does".

- [ ] **Step 7: `AGENTS.md` terms and `CHANGELOG.md`**

Add to the terms table in `AGENTS.md`:

```markdown
| SSO | Single sign-on: a person signs in through the IdP; the UI and CLI are public OAuth clients and the server verifies the IdP's JWT access token on every request. |
| IdP | The identity provider: the OAuth authorization server and OpenID provider an organisation runs. Configured once per server in `auth.oidc`. |
| Access token | The IdP-issued JWT a client presents as bearer. Its `aud` names evalkit. Verified against the IdP's JWKS; no `auth_tokens` row. |
| Subject | The pair `(issuer, sub)` identifying one identity at one IdP; keys the `users` row with the tenant. |
| Machine user | A `users` row bound to a machine client's subject: no password, no email, `kind` is `machine`. CI authenticates as one with the client credentials grant. |
| Tenant | One organisation's slice of the database; the public repo has exactly one, id 1. |
```

Under `## Unreleased` in `CHANGELOG.md`:

```markdown
- Single sign-on through any OIDC identity provider that issues JWT access
  tokens: set `auth.oidc` (`issuer`, `client_id`, `audience`, `name`) in the
  server config. `evalkit login` then opens the browser at the identity
  provider and stores its tokens, which refresh themselves;
  `--no-browser` uses the device authorization grant. The first sign-in
  creates the user; `GET /api/auth/providers` reports what the server
  offers and `POST /api/auth/oidc/register` creates the user from the ID
  token. See `docs/user/sso.md`.
- `auth.password_login` (`admin` by default when `auth.oidc` is set, or
  `all`) governs `POST /api/auth/login`; non-admins get 403 under `admin`.
  Stored password tokens keep working until they expire.
- CI authenticates as a machine client with `EVALKIT_OIDC_CLIENT_ID` and
  `EVALKIT_OIDC_CLIENT_SECRET` (client credentials grant). An admin binds
  the client's subject with `evalkit users add <name> --subject <sub>`;
  `evalkit users bind <name> --subject <sub>` links an existing user
  (`POST /api/users/{username}/identity`). `users list` shows `kind` and
  `email`; `users promote` refuses a machine user.
- `/mcp` verifies tokens with the same settings as `/api`; a token for the
  configured audience or for `<base_url>/mcp` is its evalkit user, and
  archiving the user cuts off MCP. `mcp.oauth` is removed: a config that
  still has it fails to load and names `auth.oidc`.
- `tracing.register`, `tracing.exporter` and `tracing.processor` require
  `EVALKIT_OTEL_TOKEN` (an agent token); the fallback to a login token is
  removed.
- `SyncApi` and `AsyncApi` take an `httpx.Auth` instead of a token string;
  `evalkit.client.oidc.static_auth(token)` wraps a token.
- Dependencies: `authlib` in the base package, `pyjwt[crypto]` in the
  `server` extra.
```

- [ ] **Step 8: Contracts and the docs build**

```bash
uv run python scripts/gen_schemas.py
git status --short contracts/
uv run pytest tests/test_docs.py tests/test_skills.py tests/contracts -v
uv run --group docs mkdocs build --strict
```

`gen_schemas.py` covers the wire contracts (harness, custom evaluator, cases, mapping), none of which change here, so `contracts/` should be unchanged; the command is run so the drift test is proven green. `mkdocs build --strict` fails on a broken link, which is how the new page's links are checked.

- [ ] **Step 9: Commit**

```bash
git add docs/user/sso.md docs/user/login.md docs/user/ci-sync.md docs/user/mcp.md docs/user/tracing.md docs/user/quickstart.md docs/user/sdk-runs.md docs/user/concepts.md mkdocs.yml skills/evalkit-usage/SKILL.md skills/evalkit-deployment/SKILL.md skills/evalkit-deployment/references/config-and-deploy.md skills/connect-traces/SKILL.md AGENTS.md CHANGELOG.md
git commit -m "Docs: single sign-on page, login and CI pages, MCP, tracing, skills, changelog

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 18: Full suite, PR

- [ ] **Step 1: Run everything**

```bash
uv run ruff check && uv run ruff format --check && uv run mypy src/evalkit && uv run pytest -n auto
```

Expected: all green.

- [ ] **Step 2: Open the draft PR against `develop`**

Title: `Single sign-on through an OIDC identity provider; machine clients for CI`. Body per the repo's template (TL;DR / How / Testing / Checklist), with the e2e flow result from Task 16 under Testing. `gh pr create --draft --base develop`.

---

## Self-review against the spec

- Server configuration, startup and the request verifier: Tasks 2, 4, 5, 6, 8, 10, 11.
- Registration and user lifecycle: Tasks 7, 9.
- CLI: Tasks 12, 13, 14.
- CI and machine users: Tasks 9, 12 (`ClientCredentials`), 13 (`build_auth`), 16, 17.
- SDK and trace export: Tasks 13 (`sdk/runner.py` uses `build_auth`), 15.
- Docs, changelog, versioning and tests: Tasks 1, 3, 16, 17. The MINOR version bump happens in the release PR, not here, per the repo's release rules.
- Names: `OidcConfig`, `AuthConfig.oidc`, `AuthConfig.password_login`, `OidcState`, `verify_access_token(oidc, token, audience)`, `sso_user`, `is_jwt`, `TokenSource`, `StaticToken`, `StoredOidcLogin`, `ClientCredentials`, `EvalkitAuth`, `Connection.oidc`, the three routes: all as the overview lists them. Two deviations, each stated where it happens: `sso_user` takes `(state, session, token, audience, request)`; `token_source` lives in `evalkit.client.oidc` as `build_auth`.
