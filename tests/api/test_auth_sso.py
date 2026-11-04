from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from evalkit.api.app import create_app
from evalkit.config import OidcConfig
from evalkit.db.users import UserStore
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


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def bind_user(
    env: Env, idp: FakeIdp, username: str, sub: str, email: str | None = None
) -> None:
    await UserStore(env.session_factory, 1).create(
        username, issuer=idp.issuer, subject=sub, email=email
    )


async def test_an_access_token_for_a_bound_subject_is_served(env, idp):
    await bind_user(env, idp, "ann", "sub-ann", "ann@example.com")
    async with sso_app(env, idp) as client:
        resp = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "ann" and body["email"] == "ann@example.com"
    assert body["kind"] == "human"
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
        login = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
        )
        assert login.status_code == 200
        resp = await client.get("/api/auth/me", headers=bearer(login.json()["token"]))
    assert resp.status_code == 200 and resp.json()["username"] == "admin"
    assert resp.json()["kind"] == "human" and resp.json()["email"] is None


async def test_a_jwt_without_an_idp_configured_is_401(env, tmp_path, idp):
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
        login = await client.post(
            "/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
        )
        assert login.status_code == 200


async def register(
    client: httpx.AsyncClient,
    idp: FakeIdp,
    sub: str,
    email: str | None,
    preferred: str | None = None,
    **token_kwargs,
):
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
        assert first.json() == {
            "username": "ann",
            "is_admin": False,
            "email": "ann@example.com",
            "kind": "human",
            "created": True,
        }
        second = await register(client, idp, "sub-ann", "ann@new.example.com", "ann")
        assert second.json()["created"] is False
        assert second.json()["email"] == "ann@new.example.com"
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
    assert (
        resp.json()["detail"] == "username admin is taken by a user or workspace; "
        "ask an admin to bind subject sub-9 to an account"
    )


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
