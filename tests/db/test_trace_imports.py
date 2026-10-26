from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from evalkit.contracts.enums import TraceImportStatus, TraceProvider
from evalkit.db import models
from evalkit.db.agents import AgentStore
from evalkit.db.trace_imports import (
    ImportNotActive,
    ImportRunning,
    TraceImportStore,
    UnknownTraceImport,
    reap_imports,
)
from evalkit.db.trace_sources import TraceSourceStore
from evalkit.db.users import UserStore
from tests.identity import seed_identity

SINCE = datetime(2026, 10, 1, tzinfo=UTC)
UNTIL = datetime(2026, 10, 8, tzinfo=UTC)


@pytest.fixture
async def db(session_factory):
    await seed_identity(session_factory, {"admin": "password", "akshat": "password"}, shared=())
    agent = await AgentStore(session_factory).create("akshat", "research-agent")
    source = await TraceSourceStore(session_factory, None).create(
        "phx", TraceProvider.PHOENIX, "https://phoenix.example.com", None
    )
    user = next(u for u in await UserStore(session_factory).list() if u.username == "akshat")
    store = TraceImportStore(session_factory)

    async def start(holder: str = "pod:a") -> int:
        view = await store.create(
            agent.id, source.id, "research-agent", SINCE, UNTIL, user.id, holder
        )
        return view.id

    return store, agent.id, start, session_factory


async def test_create_returns_a_running_view_and_refuses_a_second(db):
    store, agent_id, start, _ = db
    import_id = await start()
    view = await store.get(agent_id, import_id)
    assert (view.agent, view.source, view.created_by) == ("research-agent@akshat", "phx", "akshat")
    assert view.status is TraceImportStatus.RUNNING and view.resume_from == SINCE
    assert (view.traces, view.spans, view.sample_session) == (0, 0, None)
    with pytest.raises(ImportRunning, match=f"import {import_id} is already running"):
        await start()
    with pytest.raises(UnknownTraceImport):
        await store.get(agent_id + 1, import_id)


async def test_advance_checks_the_holder_and_adds_progress(db):
    store, agent_id, start, _ = db
    import_id = await start("pod:a")
    later = SINCE + timedelta(hours=5)
    assert (
        await store.advance(
            import_id,
            "pod:b",
            resume_from=later,
            resume_trace_ids=["t"],
            traces=1,
            spans=1,
            sample_session="x",
        )
        is None
    )
    for traces, sample in ((25, "s1"), (10, None)):
        status = await store.advance(
            import_id,
            "pod:a",
            resume_from=later,
            resume_trace_ids=["t"],
            traces=traces,
            spans=100,
            sample_session=sample,
        )
        assert status is TraceImportStatus.RUNNING
    view = await store.get(agent_id, import_id)
    assert (view.resume_from, view.traces, view.spans, view.sample_session) == (
        later,
        35,
        200,
        "s1",
    )


async def test_stop_is_seen_by_advance_and_only_running_imports_stop(db):
    store, agent_id, start, _ = db
    import_id = await start()
    assert (await store.stop(agent_id, import_id)).status is TraceImportStatus.STOPPING
    status = await store.advance(
        import_id,
        "pod:a",
        resume_from=SINCE,
        resume_trace_ids=[],
        traces=0,
        spans=0,
        sample_session=None,
    )
    assert status is TraceImportStatus.STOPPING
    await store.finish(import_id, "pod:a", TraceImportStatus.STOPPED)
    view = await store.get(agent_id, import_id)
    assert view.status is TraceImportStatus.STOPPED and view.finished_at is not None
    with pytest.raises(ImportNotActive, match="only a running import can be stopped"):
        await store.stop(agent_id, import_id)


async def test_restart_keeps_progress_and_takes_a_new_holder(db):
    store, agent_id, start, _ = db
    import_id = await start("pod:a")
    with pytest.raises(ImportNotActive, match="failed or stopped"):
        await store.restart(agent_id, import_id, "pod:b")
    middle = SINCE + timedelta(days=2)
    await store.advance(
        import_id,
        "pod:a",
        resume_from=middle,
        resume_trace_ids=["m"],
        traces=5,
        spans=9,
        sample_session="s",
    )
    await store.finish(import_id, "pod:a", TraceImportStatus.FAILED, "HTTPStatusError: 500")
    view = await store.restart(agent_id, import_id, "pod:b")
    assert view.status is TraceImportStatus.RUNNING and view.error is None
    assert (view.resume_from, view.traces) == (middle, 5)
    job = await store.job(import_id)
    assert (job.resume_from, job.resume_trace_ids) == (middle, ["m"])
    assert (
        await store.advance(
            import_id,
            "pod:a",
            resume_from=UNTIL,
            resume_trace_ids=[],
            traces=1,
            spans=1,
            sample_session=None,
        )
        is None
    )


async def test_reap_fails_imports_whose_lease_expired(db):
    store, agent_id, start, factory = db
    import_id = await start()
    assert await reap_imports(factory) == []
    async with factory() as session:
        await session.execute(update(models.TraceImport).values(lease_expires_at=SINCE))
        await session.commit()
    assert await reap_imports(factory) == [import_id]
    view = await store.get(agent_id, import_id)
    assert view.status is TraceImportStatus.FAILED
    assert view.error == "the server stopped while importing"


async def test_list_is_newest_first(db):
    store, agent_id, start, _ = db
    first = await start()
    await store.finish(first, "pod:a", TraceImportStatus.DONE)
    second = await start()
    assert [v.id for v in await store.recent(agent_id)] == [second, first]
    assert [v.id for v in await store.recent(agent_id, limit=1)] == [second]


async def test_renew_extends_the_lease_without_moving_progress(db):
    store, agent_id, start, factory = db
    import_id = await start("pod:a")
    async with factory() as session:
        await session.execute(update(models.TraceImport).values(lease_expires_at=SINCE))
        await session.commit()
    assert await store.renew(import_id, "pod:b") is None
    assert await store.renew(import_id, "pod:a") is TraceImportStatus.RUNNING
    assert await reap_imports(factory) == []
    view = await store.get(agent_id, import_id)
    assert (view.resume_from, view.traces, view.spans) == (SINCE, 0, 0)
    await store.stop(agent_id, import_id)
    assert await store.renew(import_id, "pod:a") is TraceImportStatus.STOPPING
