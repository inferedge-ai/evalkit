import asyncio
from datetime import UTC, datetime

from evalkit.api.trace_imports import interrupt_imports
from evalkit.contracts.enums import TraceImportStatus, TraceProvider
from evalkit.db.agents import AgentStore
from evalkit.db.trace_imports import TraceImportStore
from evalkit.db.trace_sources import TraceSourceStore
from evalkit.db.users import UserStore
from tests.identity import seed_identity, workspace_ref


async def test_shutdown_cancels_running_imports_and_marks_them_failed(session_factory):
    await seed_identity(session_factory, {"admin": "password", "akshat": "password"}, shared=())
    agent = await AgentStore(session_factory, 1).create(
        await workspace_ref(session_factory, "akshat"), "research-agent"
    )
    source = await TraceSourceStore(session_factory, 1, None).create(
        "phx", TraceProvider.PHOENIX, "https://phoenix.example.com", None
    )
    user = next(u for u in await UserStore(session_factory, 1).list() if u.username == "akshat")
    store = TraceImportStore(session_factory, 1)
    view = await store.create(
        agent.id,
        source.id,
        "research-agent",
        datetime(2026, 10, 1, tzinfo=UTC),
        datetime(2026, 10, 8, tzinfo=UTC),
        user.id,
        "pod:a",
    )
    running = asyncio.create_task(asyncio.Event().wait())
    done = asyncio.create_task(asyncio.sleep(0))
    await done
    await interrupt_imports(
        session_factory, {view.id: ("pod:a", running), view.id + 1: ("pod:b", done)}
    )
    assert running.cancelled()
    stopped = await store.get(agent.id, view.id)
    assert stopped.status is TraceImportStatus.FAILED
    assert stopped.error == "the server stopped while importing"
