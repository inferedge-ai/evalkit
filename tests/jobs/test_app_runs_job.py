from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from procrastinate import App, testing

from evalkit.config import WorkerConfig
from evalkit.db.agents import AgentStore
from evalkit.jobs.app import build_job_app
from evalkit.jobs.app_runs import SERVICES, STORE_TASK, TICK_TASK, AppRunServices, tick
from tests.app_run_world import session_spans


@dataclass
class Context:
    app: App
    additional_context: dict[str, Any]


def job_app(world):
    connector = testing.InMemoryConnector()
    app = build_job_app(world.factory.kw["bind"].url, WorkerConfig(), connector=connector)

    async def agent(tenant_id: str, agent_id: int):
        assert tenant_id == "default"
        return {a.id: a for a in (world.agent, world.harness_agent)}[agent_id]

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


async def test_two_ticks_leave_one_waiting_pass_per_agent(app_run_world):
    app, connector, context = job_app(app_run_world)
    async with app.open_async():
        await tick(Context(app, context), 0)
        await tick(Context(app, context), 300)
    assert waiting(connector) == [
        (STORE_TASK, {"tenant_id": "default", "agent_id": agent.id})
        for agent in (app_run_world.agent, app_run_world.harness_agent)
    ]


async def test_the_worker_stores_the_app_runs_a_tick_asks_for(app_run_world):
    await app_run_world.spans.upsert(app_run_world.agent.id, session_spans("chat-1", "a"))
    app_run_world.add_run("h-1", minutes_ago=10)
    app_run_world.at = datetime.now(UTC) + timedelta(seconds=60)
    app, connector, context = job_app(app_run_world)
    async with app.open_async():
        await app.configure_task(TICK_TASK).defer_async(timestamp=0)
        # One job at a time: with wait=False a second idle slot would stop the
        # worker before the tick has deferred the pass.
        await app.run_worker_async(
            wait=False, concurrency=1, install_signal_handlers=False, additional_context=context
        )
    stored = {(run.kind, run.harness_run_id) for run in await app_run_world.runs()}
    assert stored == {("app", "chat-1"), ("app", "h-1")}
    assert waiting(connector) == []


async def test_a_tick_defers_only_the_agents_whose_schedule_matches_its_minute(
    app_run_world, session_factory
):
    world = app_run_world
    await AgentStore(session_factory, "default").edit(
        world.harness_agent.ref,
        settings=world.harness_agent.settings.model_copy(update={"app_runs_cron": "0 * * * *"}),
    )
    app, connector, context = job_app(world)
    five_past = int(datetime(2026, 11, 25, 10, 5, tzinfo=UTC).timestamp())
    six_past = int(datetime(2026, 11, 25, 10, 6, tzinfo=UTC).timestamp())
    on_the_hour = int(datetime(2026, 11, 25, 11, 0, tzinfo=UTC).timestamp())
    async with app.open_async():
        await tick(Context(app, context), six_past)
        assert waiting(connector) == []
        await tick(Context(app, context), five_past)
        assert [args["agent_id"] for _, args in waiting(connector)] == [world.agent.id]
        await tick(Context(app, context), on_the_hour)
    assert [args["agent_id"] for _, args in waiting(connector)] == [
        world.agent.id,
        world.harness_agent.id,
    ]
