import base64
import hashlib
import json
import secrets
import time
from collections.abc import Iterable
from urllib.parse import parse_qs, urlencode

import httpx2
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from jwt.algorithms import RSAAlgorithm
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import EvaluatorKind, TrajectorySource, WorkspaceRole
from evalkit.contracts.settings import AgentSettings, JudgeSettings
from evalkit.core.access import WorkspaceRef
from evalkit.core.agents import AgentDefinition
from evalkit.db.agents import AgentStore
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_agents import (
    FAKE_EVALUATORS,
    FAKE_HARNESS_URL,
    FAKE_SETTINGS,
    JUDGE_URL,
    TEST_BOX,
    TEST_ENV,
)


async def seed_identity(
    session_factory: async_sessionmaker[AsyncSession],
    users: dict[str, str],
    *,
    admins: Iterable[str] = ("admin",),
    shared: Iterable[str] = ("canonical",),
    reviewers: Iterable[str] = (),
    writers: Iterable[str] = (),
) -> None:
    admins = set(admins)
    store = UserStore(session_factory, "default")
    ids = {}
    for name, password in users.items():
        ids[name] = (await store.create(name, password=password, is_admin=name in admins)).id
    workspaces = WorkspaceStore(session_factory, "default")
    granter = next(ids[name] for name in users if name in admins)
    for name in shared:
        await workspaces.create_shared(name, granter)
        for user in reviewers:
            await workspaces.add_member(name, user, WorkspaceRole.REVIEWER, granter)
        for user in writers:
            await workspaces.add_member(name, user, WorkspaceRole.WRITER, granter)
    providers = LlmProviderStore(session_factory, "default")
    await providers.create("judge", JUDGE_URL, TEST_BOX.seal(TEST_ENV["JUDGE_KEY"]))
    await providers.set_default_judge(JudgeSettings(base_url=JUDGE_URL, model="judge-model"))


async def seed_agent(
    session_factory: async_sessionmaker[AsyncSession],
    name: str = "fake",
    workspace: str = "canonical",
    *,
    harness_url: str | None = FAKE_HARNESS_URL,
    trajectory_source: TrajectorySource = TrajectorySource.ATIF,
    settings: AgentSettings = FAKE_SETTINGS,
    mapping: dict | None = None,
    tenant_id: str = "default",
) -> AgentDefinition:
    ws = await WorkspaceStore(session_factory, tenant_id).get_by_name(workspace)
    return await AgentStore(session_factory, tenant_id).create(
        ws,
        name,
        harness_url=harness_url,
        trajectory_source=trajectory_source,
        settings=settings,
        mapping=mapping,
        api_key_encrypted=TEST_BOX.seal(TEST_ENV["FAKE_AGENT_KEY"]) if harness_url else None,
    )


async def seed_evaluators(
    session_factory: async_sessionmaker[AsyncSession], workspace: str = "canonical"
) -> None:
    ws = await WorkspaceStore(session_factory, "default").get_by_name(workspace)
    store = RemoteEvaluatorStore(session_factory, "default")
    for name, settings in FAKE_EVALUATORS.items():
        await store.create(
            ws,
            name,
            EvaluatorKind.SCORE,
            settings,
            TEST_BOX.seal(TEST_ENV["CUSTOM_EVAL_KEY"]),
        )


async def workspace_ref(
    session_factory: async_sessionmaker[AsyncSession], name: str
) -> WorkspaceRef:
    return await WorkspaceStore(session_factory, "default").get_by_name(name)


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
        self.scopes: str | list[str] | None = None
        self.scope_claim = "scope"
        self.client_credentials_requests: list[dict[str, str]] = []

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
            "jti": secrets.token_hex(8),
            **({self.scope_claim: self.scopes} if self.scopes is not None else {}),
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
            "id_token": self.id_token(
                login["sub"], login["email"], login["preferred_username"], nonce=login.get("nonce")
            ),
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
            if not query.get("code_challenge") or query.get("code_challenge_method") != "S256":
                body = {"error": "invalid_request", "error_description": "pkce"}
                return 400, {"content-type": "application/json"}, json.dumps(body).encode()
            code = secrets.token_urlsafe(8)
            self.codes[code] = {
                **self._login(),
                "challenge": query.get("code_challenge"),
                "nonce": query.get("nonce"),
            }
            location = (
                f"{query['redirect_uri']}?{urlencode({'code': code, 'state': query['state']})}"
            )
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
            if challenge != login["challenge"]:
                return 400, headers, b'{"error": "invalid_grant", "error_description": "pkce"}'
            return 200, headers, json.dumps(self._issue(login)).encode()
        if grant == "refresh_token":
            login = self.refresh_tokens.pop(form.get("refresh_token", ""), None)
            if login is None:
                return 400, headers, b'{"error": "invalid_grant"}'
            return 200, headers, json.dumps(self._issue(login)).encode()
        if grant == "client_credentials":
            client_id, secret = form.get("client_id"), form.get("client_secret")
            if (
                client_id not in self.machine_clients
                or self.machine_clients[client_id][0] != secret
            ):
                return 401, headers, b'{"error": "invalid_client"}'
            self.client_credentials_requests.append(form)
            granted = {self.scope_claim: form["scope"]} if "scope" in form else None
            body = {
                "access_token": self.access_token(
                    self.machine_clients[client_id][1], extra=granted
                ),
                "expires_in": self.access_ttl,
                "token_type": "Bearer",
            }
            return 200, headers, json.dumps(body).encode()
        if grant == "urn:ietf:params:oauth:grant-type:device_code":
            pending = self.devices.get(form.get("device_code", ""))
            if pending is None:
                return 400, headers, b'{"error": "expired_token"}'
            if pending["pending"] > 0:
                pending["pending"] -= 1
                return 400, headers, b'{"error": "authorization_pending"}'
            self.devices.pop(form["device_code"])
            return 200, headers, json.dumps(self._issue(pending)).encode()
        return 400, headers, b'{"error": "unsupported_grant_type"}'

    def _handle_httpx(self, request: httpx2.Request) -> httpx2.Response:
        query = {k: v[0] for k, v in parse_qs(request.url.query.decode()).items()}
        form = (
            {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if request.content
            else {}
        )
        if request.headers.get("authorization", "").startswith("Basic "):
            user, _, password = (
                base64.b64decode(request.headers["authorization"][6:]).decode().partition(":")
            )
            form.setdefault("client_id", user)
            form.setdefault("client_secret", password)
        status, headers, body = self.handle(request.method, request.url.path, query, form)
        return httpx2.Response(status, headers=headers, content=body)

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._handle_httpx)

    def asgi(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(
            CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
        )

        @app.api_route("/{path:path}", methods=["GET", "POST"])
        async def serve(path: str, request: Request) -> Response:
            query = dict(request.query_params)
            form = (
                dict(await request.form())
                if request.headers.get("content-type", "").startswith(FORM)
                else {}
            )
            if request.headers.get("authorization", "").startswith("Basic "):
                user, _, password = (
                    base64.b64decode(request.headers["authorization"][6:]).decode().partition(":")
                )
                form.setdefault("client_id", user)
                form.setdefault("client_secret", password)
            status, headers, body = self.handle(
                request.method, f"/{path}", query, {k: str(v) for k, v in form.items()}
            )
            return Response(content=body, status_code=status, headers=headers)

        return app
