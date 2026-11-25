from datetime import UTC, datetime

from evalkit.contracts.enums import RunKind, RunStatus
from evalkit.core import app_runs
from evalkit.core.blob import trajectory_key
from evalkit.db.agents import AgentStore
from evalkit.db.app_runs import agents_with_app_runs
from evalkit.db.runs import insert_agent_run
from evalkit.store.base import AgentRunRecord


def app_run_ids(runs) -> list[str]:
    return [run.harness_run_id for run in runs if run.kind == "app"]


async def test_finished_runs_are_stored_and_a_running_one_waits(app_run_world):
    world = app_run_world
    world.add_run("r1", minutes_ago=30)
    world.add_run("r2", minutes_ago=20, status="running")
    world.add_run("r3", minutes_ago=10)
    first = await world.harness_pass()
    assert (first.stored, first.waiting, first.stopped) == (2, 1, None)
    runs = await world.runs()
    assert app_run_ids(runs) == ["r1", "r3"]
    [r1] = [run for run in runs if run.harness_run_id == "r1"]
    assert (r1.status, r1.answer, r1.trajectory_status) == (
        "completed",
        "The answer is 42.",
        "valid",
    )
    assert r1.title == "run r1" and r1.trajectory_key == trajectory_key(r1.agent_id, "r1")
    gauges = await world.gauges(r1.id)
    assert gauges["gauge_total_tool_calls"] is not None and "gauge_judge_cost_usd" not in gauges
    assert await world.cursor(r1.agent_id) == world.harness.runs["r2"]["created_at"]
    del world.harness.runs["r2"]["status"]
    second = await world.harness_pass()
    assert (second.stored, second.waiting) == (1, 0)
    assert app_run_ids(await world.runs()) == ["r1", "r3", "r2"]
    third = await world.harness_pass()
    assert third.stored == 0


async def test_a_newer_run_of_a_stored_session_replaces_its_row(app_run_world):
    world = app_run_world
    world.add_run("m1", minutes_ago=30, session_id="chat-9")
    await world.harness_pass()
    [before] = await world.runs()
    old_key = before.trajectory_key
    world.add_run("m2", minutes_ago=5, session_id="chat-9")
    result = await world.harness_pass()
    [after] = await world.runs()
    assert result.stored == 1
    assert (after.id, after.harness_run_id, after.session_id) == (before.id, "m2", "chat-9")
    assert after.trajectory_key == trajectory_key(after.agent_id, "m2")
    assert not await world.deps.blob_store.object_exists(old_key)
    assert await world.deps.blob_store.object_exists(after.trajectory_key)
    assert len(await world.gauges(after.id)) == len(await world.gauges(before.id))


async def test_an_eval_run_on_the_harness_is_not_stored_as_an_app_run(app_run_world):
    world = app_run_world
    world.add_run("e1", minutes_ago=10)
    async with world.factory() as s:
        await insert_agent_run(
            s,
            world.harness_agent.id,
            AgentRunRecord(
                harness_run_id="e1", status=RunStatus.COMPLETED, started_at=datetime.now(UTC)
            ),
            RunKind.EVAL,
        )
        await s.commit()
    result = await world.harness_pass()
    assert result.stored == 0
    assert [run.kind for run in await world.runs()] == ["eval"]


async def test_a_harness_that_keeps_failing_stops_the_pass_and_the_next_one_catches_up(
    app_run_world,
):
    world = app_run_world
    world.add_run("r1", minutes_ago=10)
    start = await world.cursor(world.harness_agent.id)
    world.harness.down_with = 503
    failed = await world.harness_pass()
    assert failed.stored == 0 and "503" in failed.stopped
    assert world.harness.down_requests == 3
    assert await world.cursor(world.harness_agent.id) == start
    world.harness.down_with = None
    assert (await world.harness_pass()).stored == 1


async def test_a_run_without_a_trajectory_is_stored_as_unavailable(app_run_world):
    world = app_run_world
    world.add_run("r1", minutes_ago=10, no_trajectory=True)
    result = await world.harness_pass()
    [run] = await world.runs()
    assert result.stored == 1
    assert (run.status, run.trajectory_status, run.trajectory_key) == (
        "completed",
        "unavailable",
        None,
    )
    assert "404" in run.trajectory_note


async def test_a_run_still_running_after_a_day_is_passed_over(app_run_world):
    world = app_run_world
    world.add_run("stuck", minutes_ago=60, status="running")
    world.add_run("r1", minutes_ago=10)
    result = await world.harness_pass(days_later=2)
    assert (result.stored, result.waiting) == (1, 0)
    assert await world.cursor(world.harness_agent.id) == world.harness.runs["r1"]["created_at"]


async def test_a_pass_stores_at_most_the_agents_limit_and_the_next_carries_on(
    app_run_world, session_factory
):
    world = app_run_world
    world.harness_agent = await AgentStore(session_factory, "default").edit(
        world.harness_agent.ref,
        settings=world.harness_agent.settings.model_copy(update={"app_runs_per_pass": 2}),
    )
    for index, minutes in enumerate((30, 20, 10)):
        world.add_run(f"r{index}", minutes_ago=minutes)
    assert (await world.harness_pass()).stored == 2
    assert app_run_ids(await world.runs()) == ["r0", "r1"]
    assert (await world.harness_pass()).stored == 1


async def test_shared_harness_agents_have_app_runs_stored_and_personal_ones_do_not(
    app_run_world, session_factory
):
    stored = await agents_with_app_runs(session_factory)
    assert [(agent.agent_id, agent.cron) for agent in stored] == [
        (app_run_world.agent.id, "*/5 * * * *"),
        (app_run_world.harness_agent.id, "*/5 * * * *"),
    ]


async def test_a_harness_that_does_not_list_runs_has_nothing_stored(app_run_world):
    world = app_run_world
    world.add_run("r1", minutes_ago=10)
    world.harness.capabilities["provides"]["run_listing"] = False
    result = await world.harness_pass()
    assert (result.stored, result.stopped) == (0, app_runs.NO_RUN_LISTING)
    assert await world.runs() == []
