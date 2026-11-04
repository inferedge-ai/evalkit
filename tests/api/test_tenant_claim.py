import pytest
from pydantic import ValidationError
from sqlalchemy import select

from evalkit.config import AdminClaimConfig, OidcConfig
from evalkit.db.models import Tenant, User
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.tenants import TenantExists, UnknownTenant, create_tenant, set_external_id
from evalkit.db.users import UserStore
from tests.api.test_auth_sso import bearer, register, sso_app
from tests.identity import FakeIdp
from tests.native import Env, seed_env

ORG = "ORG-7f3a"
ADMIN = AdminClaimConfig(name="roles", value="evalkit-admin")


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


def test_auto_create_needs_a_tenant_claim():
    with pytest.raises(ValidationError, match="tenant_auto_create needs auth.oidc.tenant_claim"):
        OidcConfig(
            issuer="https://i", client_id="c", audience="a", name="n", tenant_auto_create=True
        )


async def test_register_lands_in_the_tenant_named_by_the_claim(env, idp):
    tenant_id = await create_tenant(env.session_factory, "acme", ORG)
    async with sso_app(env, idp, tenant_claim="tenant_id", admin_claim=ADMIN) as client:
        resp = await register(
            client, idp, "sub-ann", "ann@example.com", "ann", extra={"tenant_id": ORG}
        )
        assert resp.status_code == 200, resp.text
        me = await client.get(
            "/api/auth/me",
            headers=bearer(idp.access_token("sub-ann", extra={"tenant_id": ORG})),
        )
    assert me.status_code == 200 and me.json()["username"] == "ann"
    assert await tenant_of(env, "ann") == tenant_id


async def test_an_unknown_org_is_refused_without_auto_create(env, idp):
    async with sso_app(env, idp, tenant_claim="tenant_id", admin_claim=ADMIN) as client:
        resp = await register(client, idp, "sub-ann", "ann@example.com", extra={"tenant_id": ORG})
    assert resp.status_code == 403
    assert resp.json()["detail"].startswith(f"no tenant for tenant_id {ORG}")
    async with tenant_session(env.session_factory, ALL_TENANTS) as s:
        assert await s.scalar(select(Tenant.id).where(Tenant.external_id == ORG)) is None


async def test_auto_create_makes_the_tenant_once(env, idp):
    async with sso_app(
        env, idp, tenant_claim="tenant_id", tenant_auto_create=True, admin_claim=ADMIN
    ) as client:
        first = await register(client, idp, "sub-ann", "ann@example.com", extra={"tenant_id": ORG})
        second = await register(client, idp, "sub-bo", "bo@example.com", extra={"tenant_id": ORG})
    assert first.status_code == 200 and second.status_code == 200
    assert await tenant_of(env, "ann") == await tenant_of(env, "bo") != 1
    async with tenant_session(env.session_factory, ALL_TENANTS) as s:
        rows = (await s.scalars(select(Tenant).where(Tenant.external_id == ORG))).all()
    assert [t.name for t in rows] == [ORG]


async def test_a_token_without_the_claim_is_refused(env, idp):
    async with sso_app(
        env, idp, tenant_claim="tenant_id", tenant_auto_create=True, admin_claim=ADMIN
    ) as client:
        resp = await register(client, idp, "sub-ann", "ann@example.com")
        api = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
    assert (
        resp.status_code == 403 and resp.json()["detail"] == "access token has no tenant_id claim"
    )
    assert api.status_code == 403


async def test_the_same_subject_in_another_org_is_another_user(env, idp):
    await create_tenant(env.session_factory, "acme", ORG)
    await create_tenant(env.session_factory, "globex", "ORG-other")
    async with sso_app(env, idp, tenant_claim="tenant_id", admin_claim=ADMIN) as client:
        await register(client, idp, "sub-ann", "ann@example.com", "ann", extra={"tenant_id": ORG})
        other = await client.get(
            "/api/auth/me",
            headers=bearer(idp.access_token("sub-ann", extra={"tenant_id": "ORG-other"})),
        )
    assert other.status_code == 401 and "not registered" in other.json()["detail"]


async def test_admin_claim_promotes_and_demotes_at_each_register(env, idp):
    async with sso_app(
        env, idp, tenant_claim="tenant_id", tenant_auto_create=True, admin_claim=ADMIN
    ) as client:
        org = {"tenant_id": ORG}
        await register(
            client,
            idp,
            "sub-ann",
            "ann@example.com",
            "ann",
            extra={**org, "roles": ["evalkit-admin"]},
        )
        assert await is_admin(env, "ann")
        await register(client, idp, "sub-ann", "ann@example.com", "ann", extra=org)
        assert not await is_admin(env, "ann")
        await register(
            client,
            idp,
            "sub-ann",
            "ann@example.com",
            "ann",
            extra={**org, "roles": "evalkit-admin"},
        )
        assert await is_admin(env, "ann")


async def test_without_admin_claim_register_leaves_is_admin_alone(env, idp):
    async with sso_app(env, idp) as client:
        await register(client, idp, "sub-ann", "ann@example.com", "ann")
        async with tenant_session(env.session_factory, ALL_TENANTS) as s:
            user = await s.scalar(select(User).where(User.username == "ann"))
            user.is_admin = True
            await s.commit()
        await register(client, idp, "sub-ann", "ann@example.com", "ann")
    assert await is_admin(env, "ann")


async def test_an_org_admin_manages_only_their_tenant(env, idp):
    async with sso_app(
        env, idp, tenant_claim="tenant_id", tenant_auto_create=True, admin_claim=ADMIN
    ) as client:
        claims = {"tenant_id": ORG, "roles": ["evalkit-admin"]}
        await register(client, idp, "sub-ann", "ann@example.com", "ann", extra=claims)
        headers = bearer(idp.access_token("sub-ann", extra=claims))
        users = await client.get("/api/users", headers=headers)
        demote = await client.post("/api/users/admin/demote", headers=headers)
        created = await client.post("/api/workspaces", headers=headers, json={"name": "dev"})
    assert users.status_code == 200
    assert [u["username"] for u in users.json()] == ["ann"]
    assert demote.status_code == 404
    assert created.status_code == 200 and created.json()["tenant_id"] != 1


async def test_create_tenant_refuses_a_taken_external_id_or_name(env):
    await create_tenant(env.session_factory, "acme", ORG)
    with pytest.raises(TenantExists, match="external id"):
        await create_tenant(env.session_factory, "other", ORG)
    with pytest.raises(TenantExists, match="named acme"):
        await create_tenant(env.session_factory, "acme", None)


async def test_a_machine_token_without_the_claim_finds_its_bound_user(env, idp):
    tenant_id = await create_tenant(env.session_factory, "acme", ORG)
    await UserStore(env.session_factory, tenant_id).create(
        "ci-bot", issuer=idp.issuer, subject="ci-client"
    )
    async with sso_app(env, idp, tenant_claim="tenant_id", admin_claim=ADMIN) as client:
        machine = await client.get("/api/auth/me", headers=bearer(idp.access_token("ci-client")))
        person = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
    assert machine.status_code == 200 and machine.json()["username"] == "ci-bot"
    assert person.status_code == 403
    assert person.json()["detail"] == "access token has no tenant_id claim"


async def test_a_machine_bound_in_two_tenants_needs_the_claim(env, idp):
    for name, org in (("acme", ORG), ("globex", "ORG-other")):
        tenant_id = await create_tenant(env.session_factory, name, org)
        await UserStore(env.session_factory, tenant_id).create(
            "ci-bot", issuer=idp.issuer, subject="ci-client"
        )
    async with sso_app(env, idp, tenant_claim="tenant_id", admin_claim=ADMIN) as client:
        bare = await client.get("/api/auth/me", headers=bearer(idp.access_token("ci-client")))
        named = await client.get(
            "/api/auth/me",
            headers=bearer(idp.access_token("ci-client", extra={"tenant_id": "ORG-other"})),
        )
    assert bare.status_code == 403
    assert named.status_code == 200 and named.json()["username"] == "ci-bot"


async def test_a_person_without_the_claim_is_never_found_by_subject(env, idp):
    tenant_id = await create_tenant(env.session_factory, "acme", ORG)
    await UserStore(env.session_factory, tenant_id).create(
        "ann", issuer=idp.issuer, subject="sub-ann", email="ann@example.com"
    )
    async with sso_app(env, idp, tenant_claim="tenant_id", admin_claim=ADMIN) as client:
        resp = await client.get("/api/auth/me", headers=bearer(idp.access_token("sub-ann")))
    assert resp.status_code == 403


async def test_set_external_id_lets_the_default_tenant_keep_its_people(env, idp):
    async with sso_app(env, idp) as client:
        await register(client, idp, "sub-ann", "ann@example.com", "ann")
    await set_external_id(env.session_factory, "default", ORG)
    async with sso_app(env, idp, tenant_claim="tenant_id", admin_claim=ADMIN) as client:
        me = await client.get(
            "/api/auth/me",
            headers=bearer(idp.access_token("sub-ann", extra={"tenant_id": ORG})),
        )
    assert me.status_code == 200 and me.json()["username"] == "ann"
    assert await tenant_of(env, "ann") == 1


async def test_set_external_id_refuses_an_unknown_tenant_or_a_taken_id(env):
    await create_tenant(env.session_factory, "acme", ORG)
    with pytest.raises(UnknownTenant):
        await set_external_id(env.session_factory, "nope", "ORG-x")
    with pytest.raises(TenantExists, match="already has external id"):
        await set_external_id(env.session_factory, "default", ORG)
