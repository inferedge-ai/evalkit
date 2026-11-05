import pytest
from sqlalchemy import select

from evalkit.config import AdminClaimConfig
from evalkit.db.models import User
from evalkit.db.session import ALL_TENANTS, tenant_session
from tests.api.test_auth_sso import bearer, register, sso_app
from tests.identity import FakeIdp
from tests.native import USERS, Env, seed_env

ADMIN = AdminClaimConfig(name="roles", value="evalkit-admin")


async def by_org(request, claims: dict | None) -> str | None:
    if claims is None:
        return "default"
    return claims.get("tenant_id")


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp(audience="http://evalkit.test")


@pytest.fixture
async def env(tmp_path, stub_judges, session_factory) -> Env:
    return await seed_env(session_factory, tmp_path)


async def tenant_of(env: Env, username: str) -> str:
    async with tenant_session(env.session_factory, ALL_TENANTS) as s:
        return await s.scalar(select(User.tenant_id).where(User.username == username))


async def is_admin(env: Env, username: str) -> bool:
    async with tenant_session(env.session_factory, ALL_TENANTS) as s:
        return await s.scalar(select(User.is_admin).where(User.username == username))


async def test_register_and_requests_use_the_wrappers_tenant(env, idp):
    org = {"tenant_id": "ORG-acme"}
    async with sso_app(env, idp, resolve_tenant=by_org) as client:
        resp = await register(client, idp, "sub-ann", "ann@example.com", "ann", extra=org)
        me = await client.get(
            "/api/auth/me", headers=bearer(idp.access_token("sub-ann", extra=org))
        )
        other = await client.get(
            "/api/auth/me",
            headers=bearer(idp.access_token("sub-ann", extra={"tenant_id": "ORG-globex"})),
        )
    assert resp.status_code == 200, resp.text
    assert me.status_code == 200 and me.json()["username"] == "ann"
    assert await tenant_of(env, "ann") == "ORG-acme"
    assert other.status_code == 401 and "not registered" in other.json()["detail"]


async def test_register_is_refused_without_a_tenant_or_with_a_bad_one(env, idp):
    async with sso_app(env, idp, resolve_tenant=by_org) as client:
        bare = await register(client, idp, "sub-bo", "bo@example.com")
        bad = await register(
            client, idp, "sub-ann", "ann@example.com", extra={"tenant_id": "../evil"}
        )
    assert bare.status_code == 403 and bare.json()["detail"] == "no tenant for this request"
    assert bad.status_code == 403 and "is not 1-64" in bad.json()["detail"]


async def admin_headers(client, idp, sub: str, org: str) -> dict[str, str]:
    claims = {"tenant_id": org, "roles": ["evalkit-admin"]}
    resp = await register(client, idp, sub, f"{sub}@example.com", sub, extra=claims)
    assert resp.status_code == 200, resp.text
    return bearer(idp.access_token(sub, extra=claims))


async def test_a_ci_token_without_a_tenant_cannot_use_another_tenants_binding(env, idp):
    async with sso_app(env, idp, resolve_tenant=by_org, admin_claim=ADMIN) as client:
        globex = await admin_headers(client, idp, "gail", "ORG-globex")
        await client.post(
            "/api/users", headers=globex, json={"username": "ci", "subject": "acme-ci-client"}
        )
        spaces = await client.get(
            "/api/workspaces", headers=bearer(idp.access_token("acme-ci-client"))
        )
    assert spaces.status_code == 403


async def test_a_ci_token_names_its_tenant_and_another_binding_does_not_lock_it_out(env, idp):
    async with sso_app(env, idp, resolve_tenant=by_org, admin_claim=ADMIN) as client:
        acme = await admin_headers(client, idp, "ann", "ORG-acme")
        globex = await admin_headers(client, idp, "gail", "ORG-globex")
        for headers in (acme, globex):
            await client.post(
                "/api/users", headers=headers, json={"username": "ci", "subject": "acme-ci-client"}
            )
        bare = await client.get("/api/auth/me", headers=bearer(idp.access_token("acme-ci-client")))
        named = await client.get(
            "/api/auth/me",
            headers=bearer(idp.access_token("acme-ci-client", extra={"tenant_id": "ORG-acme"})),
        )
    assert bare.status_code == 403
    assert named.status_code == 200 and named.json()["username"] == "ci"
    assert await tenant_of(env, "ann") == "ORG-acme"


async def test_password_login_is_refused_when_the_wrapper_has_no_tenant(env, idp):
    async def nobody(request, claims):
        return None

    async with sso_app(env, idp) as client:
        client.app.state.resolve_tenant = nobody  # type: ignore[attr-defined]
        resp = await client.post(
            "/api/auth/login", json={"username": "admin", "password": USERS["admin"]}
        )
    assert resp.status_code == 403 and resp.json()["detail"] == "no tenant for this request"


async def test_admin_claim_promotes_and_demotes_at_each_register(env, idp):
    async with sso_app(env, idp, admin_claim=ADMIN) as client:
        await register(
            client, idp, "sub-ann", "ann@example.com", "ann", extra={"roles": ["evalkit-admin"]}
        )
        assert await is_admin(env, "ann")
        await register(client, idp, "sub-ann", "ann@example.com", "ann")
        assert not await is_admin(env, "ann")
        await register(
            client, idp, "sub-ann", "ann@example.com", "ann", extra={"roles": "evalkit-admin"}
        )
        assert await is_admin(env, "ann")
        await register(
            client, idp, "sub-ann", "ann@example.com", "ann", extra={"roles": "not-evalkit-admin"}
        )
        assert not await is_admin(env, "ann")


async def test_without_admin_claim_register_leaves_is_admin_alone(env, idp):
    async with sso_app(env, idp) as client:
        await register(client, idp, "sub-ann", "ann@example.com", "ann")
        async with tenant_session(env.session_factory, ALL_TENANTS) as s:
            user = await s.scalar(select(User).where(User.username == "ann"))
            user.is_admin = True
            await s.commit()
        await register(client, idp, "sub-ann", "ann@example.com", "ann")
    assert await is_admin(env, "ann")


async def test_an_admin_from_the_claim_manages_only_their_tenant(env, idp):
    claims = {"tenant_id": "ORG-acme", "roles": ["evalkit-admin"]}
    async with sso_app(env, idp, resolve_tenant=by_org, admin_claim=ADMIN) as client:
        await register(client, idp, "sub-ann", "ann@example.com", "ann", extra=claims)
        headers = bearer(idp.access_token("sub-ann", extra=claims))
        users = await client.get("/api/users", headers=headers)
        demote = await client.post("/api/users/admin/demote", headers=headers)
        created = await client.post("/api/workspaces", headers=headers, json={"name": "dev"})
    assert users.status_code == 200
    assert [u["username"] for u in users.json()] == ["ann"]
    assert demote.status_code == 404
    assert created.status_code == 200 and created.json()["tenant_id"] == "ORG-acme"
