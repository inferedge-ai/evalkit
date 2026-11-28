import dataclasses
from datetime import UTC, datetime, timedelta

from evalkit.contracts.enums import RunKind, RunStatus
from evalkit.core import app_runs, pull
from evalkit.core.blob import trajectory_key
from evalkit.db.agents import AgentStore
from evalkit.db.app_runs import agents_with_app_runs
from evalkit.db.runs import insert_agent_run
from evalkit.store.base import AgentRunRecord
from tests.db.test_app_runs import FlakyBlobStore


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
    assert await world.cursor(world.harness_agent.id) > world.harness.runs["r1"]["created_at"]


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


async def test_a_harness_run_stored_without_a_trajectory_is_read_again_when_listed(
    app_run_world,
):
    world = app_run_world
    world.add_run("r1", minutes_ago=10, no_trajectory=True)
    await world.harness_pass()
    [unread] = await world.runs()
    del world.harness.runs["r1"]["no_trajectory"]
    assert (await world.harness_pass()).stored == 1
    [run] = await world.runs()
    assert (run.id, run.trajectory_status, run.trajectory_note) == (unread.id, "valid", None)
    assert (await world.harness_pass()).stored == 0


async def test_a_long_multi_line_title_is_stored_as_its_first_200_characters(app_run_world):
    world = app_run_world
    world.add_run("r1", minutes_ago=10)
    world.harness.titles["r1"] = "Build a dashboard " + "x" * 400 + "\nsecond line"
    assert (await world.harness_pass()).stored == 1
    [run] = await world.runs()
    assert len(run.title) == 200 and run.title.startswith("Build a dashboard x")


async def test_one_harness_run_that_cannot_be_stored_is_recorded_and_the_rest_are_stored(
    app_run_world,
):
    world = app_run_world
    blob = FlakyBlobStore()
    blob.refuse = {"r2"}
    world.deps = dataclasses.replace(world.deps, blob_store=blob)
    for index, minutes in enumerate((30, 20, 10), start=1):
        world.add_run(f"r{index}", minutes_ago=minutes)
    result = await world.harness_pass()
    assert (result.stored, result.failed, result.stopped) == (2, 1, None)
    runs = {run.harness_run_id: run for run in await world.runs()}
    assert runs["r2"].trajectory_status == "unavailable"
    assert runs["r2"].trajectory_note.startswith("evalkit could not store this run: OSError")
    assert await world.cursor(world.harness_agent.id) == world.harness.runs["r3"]["created_at"]


async def test_a_run_the_harness_cannot_serve_is_asked_again_and_the_others_are_stored(
    app_run_world,
):
    world = app_run_world
    world.add_run("r1", minutes_ago=30)
    world.add_run("r2", minutes_ago=20, trajectory_error=True)
    world.add_run("r3", minutes_ago=10)
    first = await world.harness_pass()
    assert (first.stored, first.failed, first.stopped) == (2, 1, None)
    assert app_run_ids(await world.runs()) == ["r1", "r3"]
    assert await world.cursor(world.harness_agent.id) == world.harness.runs["r2"]["created_at"]
    world.add_run("r4", minutes_ago=5)
    second = await world.harness_pass()
    assert (second.stored, second.failed, second.stopped) == (1, 1, None)
    later = await world.harness_pass(days_later=2)
    assert (later.stored, later.failed, later.stopped) == (0, 1, None)
    runs = {run.harness_run_id: run for run in await world.runs()}
    assert runs["r2"].trajectory_status == "unavailable" and "500" in runs["r2"].trajectory_note
    assert await world.cursor(world.harness_agent.id) > world.harness.runs["r4"]["created_at"]


async def test_three_runs_in_a_row_the_harness_cannot_serve_stop_the_pass(app_run_world):
    world = app_run_world
    for index, minutes in enumerate((40, 30, 20, 10), start=1):
        world.add_run(f"r{index}", minutes_ago=minutes, trajectory_error=True)
    start = await world.cursor(world.harness_agent.id)
    result = await world.harness_pass()
    assert (result.stored, result.failed) == (0, 3) and "500" in result.stopped
    assert await world.cursor(world.harness_agent.id) == start
    assert await world.runs() == []


async def test_three_old_runs_the_harness_never_serves_do_not_hold_the_runs_after_them(
    app_run_world,
):
    world = app_run_world
    day = 24 * 60
    for index in (1, 2, 3):
        world.add_run(f"r{index}", minutes_ago=3 * day - index, trajectory_error=True)
    world.add_run("r4", minutes_ago=3 * day - 4)
    start = datetime.now(UTC) - timedelta(days=4)
    await world.deps.store.move_cursor(world.harness_agent.id, start)
    for _ in range(2):
        result = await world.harness_pass()
        assert (result.stored, result.failed) == (0, 3) and "500" in result.stopped
        assert await world.cursor(world.harness_agent.id) == start
    third = await world.harness_pass()
    assert (third.stored, third.failed, third.stopped) == (1, 0, None)
    runs = {run.harness_run_id: run.trajectory_status for run in await world.runs()}
    assert runs == {"r1": "invalid", "r2": "invalid", "r3": "invalid", "r4": "valid"}


async def test_a_backlog_larger_than_one_listing_is_stored_oldest_first_with_none_lost(
    app_run_world, session_factory, monkeypatch
):
    world = app_run_world
    monkeypatch.setattr(pull, "MAX_HARNESS_SCAN", 2)
    monkeypatch.setattr(app_runs, "MAX_HARNESS_SCAN", 2)
    world.harness.page_cap = 1
    world.harness_agent = await AgentStore(session_factory, "default").edit(
        world.harness_agent.ref,
        settings=world.harness_agent.settings.model_copy(update={"app_runs_per_pass": 3}),
    )
    await world.deps.store.move_cursor(
        world.harness_agent.id, datetime.now(UTC) - timedelta(days=3)
    )
    ages = (4000, 3990, 3980, 3970, 1500, 30, 20, 10)
    for index, minutes in enumerate(ages):
        world.add_run(f"r{index}", minutes_ago=minutes)
    assert (await world.harness_pass()).stored == 3
    assert app_run_ids(await world.runs()) == ["r0", "r1", "r2"]
    assert (await world.harness_pass()).stored == 3
    assert (await world.harness_pass()).stored == 2
    assert app_run_ids(await world.runs()) == [f"r{index}" for index in range(len(ages))]
    assert (await world.harness_pass()).stored == 0


async def test_more_runs_in_one_minute_than_a_listing_holds_stop_the_pass(
    app_run_world, monkeypatch
):
    world = app_run_world
    monkeypatch.setattr(pull, "MAX_HARNESS_SCAN", 2)
    monkeypatch.setattr(app_runs, "MAX_HARNESS_SCAN", 2)
    world.harness.page_cap = 1
    created = datetime.now(UTC) - timedelta(minutes=10)
    for index in range(4):
        world.add_run(f"r{index}", minutes_ago=0, created_at=created + timedelta(seconds=index))
    result = await world.harness_pass()
    assert (result.stored, result.stopped) == (0, app_runs.TOO_MANY_RUNS)
    assert await world.cursor(world.harness_agent.id) <= created


async def test_a_harness_that_ignores_until_stops_the_pass_with_the_reason(app_run_world):
    world = app_run_world
    world.harness.ignore_time_filter = True
    await world.deps.store.move_cursor(
        world.harness_agent.id, datetime.now(UTC) - timedelta(days=1)
    )
    world.add_run("r1", minutes_ago=23 * 60)
    world.add_run("r2", minutes_ago=10)
    start = await world.cursor(world.harness_agent.id)
    result = await world.harness_pass()
    assert (result.stored, result.stopped) == (0, app_runs.IGNORES_UNTIL)
    assert await world.cursor(world.harness_agent.id) == start
    assert await world.runs() == []
