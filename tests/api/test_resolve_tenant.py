import pytest
from sqlalchemy import select

from evalkit.config import AdminClaimConfig
from evalkit.db.models import User
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.users import UserStore
from tests.api.test_auth_sso import bearer, register, sso_app
from tests.identity import FakeIdp
from tests.native import USERS, Env, seed_env

ADMIN = AdminClaimConfig(name="roles", value="evalkit-admin")
ORGS = {"ORG-acme": 2, "ORG-globex": 3}


async def by_org(request, claims: dict | None) -> int | None:
    if claims is None:
        return 1
    org = claims.get("tenant_id")
    return ORGS.get(org) if org is not None else None


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp(audience="http://evalkit.test")


@pytest.fixture
async def env(tmp_path, stub_judges, session_factory) -> Env:
    return await seed_env(session_factory, tmp_path)


async def tenant_of(env: Env, username: str) -> int:
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
    assert await tenant_of(env, "ann") == 2
    assert other.status_code == 401 and "not registered" in other.json()["detail"]


async def test_register_is_refused_when_the_wrapper_has_no_tenant(env, idp):
    async with sso_app(env, idp, resolve_tenant=by_org) as client:
        unknown = await register(
            client, idp, "sub-ann", "ann@example.com", extra={"tenant_id": "ORG-nope"}
        )
        bare = await register(client, idp, "sub-bo", "bo@example.com")
    assert unknown.status_code == 403 and bare.status_code == 403
    assert unknown.json()["detail"] == "no tenant for this access token"


async def test_a_machine_token_without_a_tenant_finds_its_bound_user(env, idp):
    await UserStore(env.session_factory, 2).create("ci-bot", issuer=idp.issuer, subject="ci-client")
    await UserStore(env.session_factory, 2).create(
        "ann", issuer=idp.issuer, subject="sub-ann", email="ann@example.com"
    )
    async with sso_app(env, idp, resolve_tenant=by_org) as client:
        machine = await client.get("/api/auth/me", headers=bearer(idp.access_token("ci-client")))
        person = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
    assert machine.status_code == 200 and machine.json()["username"] == "ci-bot"
    assert person.status_code == 403
    assert person.json()["detail"] == "no tenant for this access token"


async def test_a_machine_bound_in_two_tenants_needs_a_tenant(env, idp):
    for tenant_id in (2, 3):
        await UserStore(env.session_factory, tenant_id).create(
            "ci-bot", issuer=idp.issuer, subject="ci-client"
        )
    async with sso_app(env, idp, resolve_tenant=by_org) as client:
        bare = await client.get("/api/auth/me", headers=bearer(idp.access_token("ci-client")))
        named = await client.get(
            "/api/auth/me",
            headers=bearer(idp.access_token("ci-client", extra={"tenant_id": "ORG-globex"})),
        )
    assert bare.status_code == 403
    assert named.status_code == 200 and named.json()["username"] == "ci-bot"


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
    assert created.status_code == 200 and created.json()["tenant_id"] == 2
