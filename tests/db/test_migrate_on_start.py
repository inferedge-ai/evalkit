import asyncio

import httpx2
import pytest
from sqlalchemy import create_engine, text

from evalkit.api.app import create_app
from evalkit.config import AdminClaimConfig, AuthConfig, DbConfig, OidcConfig
from evalkit.core.runner import RunnerDeps
from evalkit.db.session import head_revision, make_session_factory, migrate_under_lock
from evalkit.db.store import PgStore
from tests.fake_agents import FakeAgents, FakeEvaluators, fake_credentials
from tests.fake_blob import DictBlobStore
from tests.identity import FakeIdp
from tests.native import make_cfg


def revision(db_url: str) -> str | None:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    return current


def app_for(
    tmp_path,
    db_url: str,
    migrate_on_start: bool,
    auth: AuthConfig | None = None,
    idp: FakeIdp | None = None,
):
    update: dict = {"db": DbConfig(url=db_url, migrate_on_start=migrate_on_start)}
    if auth is not None:
        update["auth"] = auth
    cfg = make_cfg(tmp_path).model_copy(update=update)
    factory = make_session_factory(db_url)
    deps = RunnerDeps(
        cfg=cfg,
        store=PgStore(factory, "default"),
        blob_store=DictBlobStore(),
        agents=FakeAgents(),
        evaluators=FakeEvaluators(),
        credentials=fake_credentials(),
    )
    return create_app(
        cfg,
        runner_deps=deps,
        session_factory=factory,
        check_schema=True,
        oidc_transport=idp.transport() if idp is not None else None,
    )


async def test_two_starts_migrate_a_fresh_database_one_after_the_other(fresh_db_url):
    await asyncio.gather(migrate_under_lock(fresh_db_url), migrate_under_lock(fresh_db_url))
    assert revision(fresh_db_url) == head_revision()


async def test_migrate_on_start_brings_a_fresh_database_to_head(
    tmp_path, fresh_db_url, monkeypatch
):
    monkeypatch.setenv("EVALKIT_ADMIN_PASSWORD", "first-start")
    app = app_for(tmp_path, fresh_db_url, migrate_on_start=True)
    async with app.router.lifespan_context(app):
        assert revision(fresh_db_url) == head_revision()


async def test_without_migrate_on_start_a_fresh_database_stops_the_server(tmp_path, fresh_db_url):
    app = app_for(tmp_path, fresh_db_url, migrate_on_start=False)
    with pytest.raises(RuntimeError, match="evalkit db upgrade"):
        async with app.router.lifespan_context(app):
            pass


async def test_a_fresh_server_with_passwords_off_starts_without_an_admin_password(
    tmp_path, fresh_db_url, monkeypatch
):
    monkeypatch.delenv("EVALKIT_ADMIN_PASSWORD", raising=False)
    idp = FakeIdp(audience="http://evalkit.test")
    oidc = OidcConfig(
        issuer=idp.issuer,
        client_id=idp.client_id,
        audience=idp.audience,
        name="Fake",
        admin_claim=AdminClaimConfig(name="roles", value="evalkit-admin"),
    )
    auth = AuthConfig(password_login="none", oidc=oidc)
    app = app_for(tmp_path, fresh_db_url, migrate_on_start=True, auth=auth, idp=idp)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test"
        ) as client:
            providers = (await client.get("/api/auth/providers")).json()
    assert providers["password"] == "none"
