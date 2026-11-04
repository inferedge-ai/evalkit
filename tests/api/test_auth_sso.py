from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from evalkit.api.app import create_app
from evalkit.config import OidcConfig
from tests.identity import FakeIdp
from tests.native import HOLDER, Env, seed_env, serve


@asynccontextmanager
async def sso_app(
    env: Env,
    idp: FakeIdp,
    *,
    password_login: str = "admin",
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[httpx.AsyncClient]:
    env.deps.cfg.auth.oidc = OidcConfig(
        issuer=idp.issuer, client_id=idp.client_id, audience=idp.audience, name="Fake"
    )
    env.deps.cfg.auth.password_login = password_login
    app = create_app(
        env.deps.cfg,
        blob_store=env.blob,
        runner_deps=env.deps,
        session_factory=env.session_factory,
        check_schema=False,
        holder=HOLDER,
        oidc_transport=transport or idp.transport(),
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp(audience="http://evalkit.test")


@pytest.fixture
async def env(tmp_path, stub_judges, session_factory) -> Env:
    return await seed_env(session_factory, tmp_path)


async def test_providers_without_an_idp(env, tmp_path):
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
