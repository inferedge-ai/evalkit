import secrets
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import create_engine, inspect, select, text, update

from evalkit.api import auth as auth_module
from evalkit.api.auth import bootstrap_admin, router
from evalkit.config import AuthConfig
from evalkit.db.models import AuthToken, User
from evalkit.db.session import head_revision, run_migrations
from evalkit.db.users import InvalidPassword, UserStore, hash_password
from tests.identity import seed_identity


class FakeCfg:
    auth = AuthConfig(token_ttl_hours=720)


@pytest.fixture
async def app(session_factory):
    application = FastAPI()
    application.include_router(router)
    application.state.cfg = FakeCfg()
    application.state.session_factory = session_factory
    await seed_identity(session_factory, {"admin": "hunter2-pw", "alice": "wonderland"}, shared=())
    return application


@pytest.fixture
async def client(app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(client, username="alice", password="wonderland") -> str:
    resp = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


async def test_login_happy_path(app, client):
    resp = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "wonderland"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"] and body["is_admin"] is False
    async with app.state.session_factory() as session:
        row = (await session.execute(select(AuthToken))).scalars().one()
        assert row.token_hash != body["token"]


async def test_download_ticket_is_single_use_and_short_lived(app, client):
    token = await login(client)
    bearer = {"Authorization": f"Bearer {token}"}
    ticket = (await client.post("/api/auth/download-ticket", headers=bearer)).json()["ticket"]
    first = await client.get("/api/auth/me", params={"ticket": ticket})
    assert first.status_code == 200 and first.json()["username"] == "alice"
    second = await client.get("/api/auth/me", params={"ticket": ticket})
    assert second.status_code == 401
    # The bearer still works, and a bearer beats a ticket in the same request.
    both = await client.get("/api/auth/me", params={"ticket": "junk"}, headers=bearer)
    assert both.status_code == 200
    # A ticket never authorises anything but a GET.
    fresh = (await client.post("/api/auth/download-ticket", headers=bearer)).json()["ticket"]
    assert (await client.post("/api/auth/logout", params={"ticket": fresh})).status_code == 401
    stale = (await client.post("/api/auth/download-ticket", headers=bearer)).json()["ticket"]
    async with app.state.session_factory() as session:
        await session.execute(
            update(AuthToken)
            .where(AuthToken.token_hash == auth_module.token_hash(stale))
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
    assert (await client.get("/api/auth/me", params={"ticket": stale})).status_code == 401
    # Issuing a ticket sweeps expired rows, so the stale one is gone, not just rejected.
    await client.post("/api/auth/download-ticket", headers=bearer)
    async with app.state.session_factory() as session:
        hashes = (await session.execute(select(AuthToken.token_hash))).scalars().all()
    assert auth_module.token_hash(stale) not in hashes


async def test_download_ticket_is_not_a_bearer_token(app, client):
    bearer = {"Authorization": f"Bearer {await login(client)}"}
    ticket = (await client.post("/api/auth/download-ticket", headers=bearer)).json()["ticket"]
    as_bearer = {"Authorization": f"Bearer {ticket}"}
    resp = await client.get("/api/auth/me", headers=as_bearer)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "a download ticket is not a bearer token"
    assert (await client.post("/api/auth/logout", headers=as_bearer)).status_code == 401
    assert (await client.get("/api/auth/me", params={"ticket": ticket})).status_code == 200


async def test_user_token_is_not_a_download_ticket(client):
    token = await login(client)
    resp = await client.get("/api/auth/me", params={"ticket": token})
    assert resp.status_code == 401 and resp.json()["detail"] == "not a download ticket"


async def test_wrong_password(client):
    resp = await client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert resp.status_code == 401


async def test_me_and_last_used(app, client):
    token = await login(client)
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    body = resp.json()
    expires = datetime.fromisoformat(body.pop("token_expires_at"))
    assert body == {
        "username": "alice",
        "is_admin": False,
        "personal_workspace": "alice",
        "roles": {},
    }
    assert timedelta(hours=719) < expires - datetime.now(UTC) <= timedelta(hours=720)
    async with app.state.session_factory() as session:
        row = (await session.execute(select(AuthToken))).scalars().one()
        assert row.last_used_at is not None


async def test_expired_token(app, client):
    token = await login(client)
    async with app.state.session_factory() as session:
        await session.execute(
            update(AuthToken).values(expires_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await session.commit()
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_logout_revokes(client):
    token = await login(client)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.post("/api/auth/logout", headers=headers)).status_code == 200
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401


async def test_archived_user_cannot_log_in_or_use_a_token(client, session_factory):
    token = await login(client)
    await UserStore(session_factory).archive("alice")
    resp = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "wonderland"}
    )
    assert resp.status_code == 401 and resp.json()["detail"] == "user is archived"
    wrong = await client.post("/api/auth/login", json={"username": "alice", "password": "nope"})
    assert wrong.json()["detail"] == "invalid username or password"
    resp = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_archived_placeholder_user_gets_401_not_500(client, session_factory):
    await UserStore(session_factory).archive("alice")
    placeholder = hash_password(secrets.token_urlsafe(32))
    async with session_factory() as s:
        await s.execute(
            update(User).where(User.username == "alice").values(password_hash=placeholder)
        )
        await s.commit()
    resp = await client.post("/api/auth/login", json={"username": "alice", "password": "x"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid username or password"
    await UserStore(session_factory).restore("alice")
    await UserStore(session_factory).set_password("alice", "fresh-password")
    assert await login(client, "alice", "fresh-password")


async def test_bootstrap_admin_only_when_empty(session_factory):
    factory = session_factory
    with pytest.raises(RuntimeError, match="EVALKIT_ADMIN_PASSWORD is not set"):
        await bootstrap_admin(factory, None)
    async with factory() as session:
        assert (await session.execute(select(User))).scalars().all() == []
    await bootstrap_admin(factory, "secret-password")
    async with factory() as session:
        users = (await session.execute(select(User))).scalars().all()
        assert [u.username for u in users] == ["admin"]
    await bootstrap_admin(factory, "another-password")
    await bootstrap_admin(factory, None)
    async with factory() as session:
        assert len((await session.execute(select(User))).scalars().all()) == 1


def test_run_migrations_postgres(fresh_db_url):
    run_migrations(fresh_db_url)
    engine = create_engine(fresh_db_url)
    with engine.connect() as conn:
        tables = set(inspect(conn).get_table_names())
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    assert {"users", "auth_tokens", "spans", "span_mappings"} <= tables
    assert version == head_revision()


async def test_unknown_user_login_still_checks_a_password(client, monkeypatch):
    checked = []
    real = auth_module.verify_password
    monkeypatch.setattr(
        auth_module, "verify_password", lambda pw, h: checked.append(h) or real(pw, h)
    )
    resp = await client.post("/api/auth/login", json={"username": "ghost", "password": "nope"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid username or password"
    assert checked == [auth_module._dummy_password_hash()]


async def test_login_rejects_passwords_over_72_bytes(client):
    resp = await client.post("/api/auth/login", json={"username": "alice", "password": "é" * 37})
    assert resp.status_code == 400
    assert resp.json()["detail"] == "password is longer than 72 bytes"


async def test_login_accepts_short_passwords(client, session_factory):
    async with session_factory() as s:
        await s.execute(
            update(User).where(User.username == "alice").values(password_hash=hash_password("pw"))
        )
        await s.commit()
    assert await login(client, "alice", "pw")


@pytest.mark.parametrize("password", ["short", "x" * 73])
async def test_bootstrap_admin_refuses_a_bad_password(session_factory, password):
    with pytest.raises(InvalidPassword):
        await bootstrap_admin(session_factory, password)
