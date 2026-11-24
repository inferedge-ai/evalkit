from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from procrastinate import App, testing

from evalkit.jobs.app import build_job_app
from evalkit.jobs.app_runs import SERVICES, STORE_TASK, TICK_TASK, AppRunServices, tick
from tests.app_run_world import session_spans


@dataclass
class Context:
    app: App
    additional_context: dict[str, Any]


def job_app(world):
    connector = testing.InMemoryConnector()
    app = build_job_app(world.factory.kw["bind"].url, connector=connector)

    async def agent(tenant_id: str, agent_id: int):
        assert (tenant_id, agent_id) == ("default", world.agent.id)
        return world.agent

    services = AppRunServices(
        session_factory=world.factory, deps=lambda tenant_id: world.deps, agent=agent
    )
    return app, connector, {SERVICES: services}


def waiting(connector) -> list[tuple[str, dict]]:
    return [
        (job["task_name"], job["args"])
        for job in connector.jobs.values()
        if job["status"] == "todo"
    ]


async def test_two_ticks_leave_one_waiting_pass_per_otel_agent(app_run_world):
    app, connector, context = job_app(app_run_world)
    async with app.open_async():
        await tick(Context(app, context), 1)
        await tick(Context(app, context), 2)
    assert waiting(connector) == [
        (STORE_TASK, {"tenant_id": "default", "agent_id": app_run_world.agent.id})
    ]


async def test_the_worker_stores_the_app_runs_a_tick_asks_for(app_run_world):
    await app_run_world.spans.upsert(app_run_world.agent.id, session_spans("chat-1", "a"))
    app_run_world.at = datetime.now(UTC) + timedelta(seconds=60)
    app, connector, context = job_app(app_run_world)
    async with app.open_async():
        await app.configure_task(TICK_TASK).defer_async(timestamp=1)
        await app.run_worker_async(
            wait=False, install_signal_handlers=False, additional_context=context
        )
    [run] = await app_run_world.runs()
    assert (run.kind, run.harness_run_id) == ("app", "chat-1")
    assert waiting(connector) == []
