import asyncio

import pytest
from sqlalchemy import create_engine, text

from evalkit.api.app import create_app
from evalkit.config import DbConfig
from evalkit.core.runner import RunnerDeps
from evalkit.db.session import head_revision, make_session_factory, migrate_under_lock
from evalkit.db.store import PgStore
from tests.fake_agents import FakeAgents, FakeEvaluators, fake_credentials
from tests.fake_blob import DictBlobStore
from tests.native import make_cfg


def revision(db_url: str) -> str | None:
    engine = create_engine(db_url)
    with engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    return current


def app_for(tmp_path, db_url: str, migrate_on_start: bool):
    cfg = make_cfg(tmp_path).model_copy(
        update={"db": DbConfig(url=db_url, migrate_on_start=migrate_on_start)}
    )
    factory = make_session_factory(db_url)
    deps = RunnerDeps(
        cfg=cfg,
        store=PgStore(factory),
        blob_store=DictBlobStore(),
        agents=FakeAgents(),
        evaluators=FakeEvaluators(),
        credentials=fake_credentials(),
    )
    return create_app(cfg, runner_deps=deps, session_factory=factory, check_schema=True)


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
