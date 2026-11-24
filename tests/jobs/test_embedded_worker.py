import asyncio

from sqlalchemy import select, text

from evalkit.api.app import create_app
from evalkit.config import DbConfig, OtelConfig, WorkerConfig
from evalkit.contracts.enums import TrajectorySource
from evalkit.db.agents import AgentStore
from evalkit.db.models import AgentRun, Evaluation
from evalkit.db.session import make_session_factory, tenant_session
from evalkit.db.spans import SpanStore
from evalkit.jobs.app_runs import STORE_TASK
from tests.app_run_world import session_spans
from tests.fake_agents import reference_mapping
from tests.identity import seed_identity, workspace_ref
from tests.native import make_cfg

WAIT_SECONDS = 15


async def stored_runs(factory) -> list[tuple[str, str]]:
    async with tenant_session(factory, "default") as s:
        rows = await s.execute(select(AgentRun.kind, AgentRun.harness_run_id))
        return [tuple(row) for row in rows.all()]


async def store_job_statuses(factory) -> list[str]:
    async with factory.kw["bind"].connect() as conn:
        rows = await conn.execute(
            text("SELECT status::text FROM procrastinate_jobs WHERE task_name = :name"),
            {"name": STORE_TASK},
        )
        return [row[0] for row in rows.all()]


async def test_the_server_runs_a_worker_that_stores_app_runs(tmp_path, db_url, monkeypatch):
    monkeypatch.setenv("EVALKIT_ADMIN_PASSWORD", "first-start")
    cfg = make_cfg(tmp_path).model_copy(
        update={
            "db": DbConfig(url=db_url),
            "worker": WorkerConfig(embedded=True),
            "otel": OtelConfig(settle_seconds=0),
        }
    )
    factory = make_session_factory(db_url)
    await seed_identity(factory, {"admin": "password", "akshat": "password"}, shared=())
    agent = await AgentStore(factory, "default").create(
        await workspace_ref(factory, "akshat"),
        "support-bot",
        trajectory_source=TrajectorySource.OTEL,
        mapping=reference_mapping(),
    )
    await SpanStore(factory, "default").upsert(agent.id, session_spans("chat-1", "a"))
    app = create_app(cfg, session_factory=factory)
    # A worker that starts defers the tick of the current 5-minute period at once.
    async with app.router.lifespan_context(app):
        async with asyncio.timeout(WAIT_SECONDS):
            while not await stored_runs(factory):
                await asyncio.sleep(0.1)
            while "succeeded" not in await store_job_statuses(factory):
                await asyncio.sleep(0.1)
    assert await stored_runs(factory) == [("app", "chat-1")]
    assert await store_job_statuses(factory) == ["succeeded"]
    async with tenant_session(factory, "default") as s:
        gauges = await s.scalars(select(Evaluation.name))
        assert "gauge_turns" in set(gauges.all())
