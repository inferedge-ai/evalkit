import dataclasses
from datetime import UTC, datetime

from evalkit.contracts.enums import RunKind, RunStatus, TraceProvider
from evalkit.contracts.settings import AgentSettings
from evalkit.core.app_runs import TOO_MANY_FAILURES
from evalkit.core.traces.mapping import SDK_ROOT_ATTRIBUTE
from evalkit.db.agents import AgentStore
from evalkit.db.mappings import MappingStore, activate_document
from evalkit.db.runs import insert_agent_run
from evalkit.db.trace_sources import TraceSourceStore
from evalkit.store.base import AgentRunRecord
from tests.app_run_world import session_spans
from tests.fake_agents import reference_mapping
from tests.fake_blob import DictBlobStore

GAUGES = {
    "gauge_duration_seconds",
    "gauge_input_tokens",
    "gauge_output_tokens",
    "gauge_cached_tokens",
    "gauge_agent_cost_usd",
    "gauge_total_tool_calls",
    "gauge_errored_tool_calls",
    "gauge_subagents",
    "gauge_turns",
}


async def test_a_settled_session_is_stored_once_with_its_gauges(app_run_world):
    await app_run_world.spans.upsert(app_run_world.agent.id, session_spans("chat-1", "a"))
    first = await app_run_world.run_pass(after_seconds=60)
    again = await app_run_world.run_pass(after_seconds=120)
    assert (first.stored, again.stored) == (1, 0)
    [run] = await app_run_world.runs()
    assert (run.kind, run.harness_run_id, run.status) == ("app", "chat-1", "completed")
    assert run.trajectory_status == "valid" and run.trajectory_key
    assert run.answer == "main step 2" and run.title == "main question"
    gauges = await app_run_world.gauges(run.id)
    assert set(gauges) >= GAUGES and "gauge_judge_cost_usd" not in gauges
    assert gauges["gauge_total_tool_calls"] == 4 and gauges["gauge_input_tokens"]


async def test_an_unsettled_session_waits_for_a_later_pass(app_run_world):
    await app_run_world.spans.upsert(app_run_world.agent.id, session_spans("chat-1", "a"))
    early = await app_run_world.run_pass(after_seconds=1)
    assert (early.stored, early.waiting) == (0, 1)
    assert await app_run_world.runs() == []
    later = await app_run_world.run_pass(after_seconds=60)
    assert later.stored == 1


async def test_a_session_that_arrives_after_a_pass_is_stored_by_the_next(app_run_world):
    await app_run_world.spans.upsert(app_run_world.agent.id, session_spans("chat-1", "a"))
    await app_run_world.run_pass(after_seconds=60)
    await app_run_world.spans.upsert(app_run_world.agent.id, session_spans("chat-2", "b"))
    second = await app_run_world.run_pass(after_seconds=120)
    assert second.stored == 1
    assert [run.harness_run_id for run in await app_run_world.runs()] == ["chat-1", "chat-2"]


async def test_eval_sessions_are_not_stored_as_app_runs(app_run_world):
    await app_run_world.spans.upsert(app_run_world.agent.id, session_spans("eval-1", "a"))
    sdk = session_spans("sdk-1", "b")
    sdk[0] = sdk[0].model_copy(
        update={"attributes": {**sdk[0].attributes, SDK_ROOT_ATTRIBUTE: True}}
    )
    await app_run_world.spans.upsert(app_run_world.agent.id, sdk)
    async with app_run_world.factory() as s:
        await insert_agent_run(
            s,
            app_run_world.agent.id,
            AgentRunRecord(
                harness_run_id="eval-1", status=RunStatus.COMPLETED, started_at=datetime.now(UTC)
            ),
            RunKind.EVAL,
        )
        await s.commit()
    result = await app_run_world.run_pass(after_seconds=60)
    assert result.stored == 0
    assert [run.kind for run in await app_run_world.runs()] == ["eval"]


async def test_a_session_with_no_steps_is_stored_as_unavailable(app_run_world):
    root = session_spans("empty-1", "a")[0]
    await app_run_world.spans.upsert(app_run_world.agent.id, [root])
    result = await app_run_world.run_pass(after_seconds=60)
    [run] = await app_run_world.runs()
    assert result.stored == 1
    assert run.trajectory_status == "unavailable" and run.trajectory_note


async def test_a_pass_imports_new_traces_only_for_an_agent_with_a_trace_source(
    app_run_world, session_factory
):
    world = app_run_world
    await world.run_pass(after_seconds=60)
    assert world.imports == []
    await TraceSourceStore(session_factory, "default", None).create(
        "phx", TraceProvider.PHOENIX, "https://phoenix.example.com", None
    )
    world.agent = await AgentStore(session_factory, "default").edit(
        world.agent.ref, settings=AgentSettings(trace_source="phx", project="support")
    )
    cursor = await world.cursor(world.agent.id)
    await world.run_pass(after_seconds=120)
    assert world.imports == [(world.agent.ref, cursor, world.at)]


async def test_a_run_stored_without_a_trajectory_is_read_again_when_a_pass_lists_it(
    app_run_world, session_factory
):
    world = app_run_world
    mappings = MappingStore(session_factory, "default")
    good = (await mappings.active(world.agent.id)).document
    async with session_factory() as s:
        await activate_document(s, world.agent.id, reference_mapping("gen_ai"), None)
        await s.commit()
    await world.spans.upsert(world.agent.id, session_spans("chat-1", "a"))
    since = await world.cursor(world.agent.id)
    await world.run_pass(after_seconds=60)
    [unread] = await world.runs()
    assert unread.trajectory_status == "unavailable"
    assert "read no LLM or tool steps" in unread.trajectory_note
    assert "evalkit traces check" in unread.trajectory_note
    async with session_factory() as s:
        await activate_document(s, world.agent.id, good, None)
        await s.commit()
    assert (await world.run_pass(after_seconds=120)).stored == 0
    await world.deps.store.move_cursor(world.agent.id, since)
    assert (await world.run_pass(after_seconds=180)).stored == 1
    [repaired] = await world.runs()
    assert (repaired.id, repaired.trajectory_status) == (unread.id, "valid")
    assert repaired.trajectory_note is None and repaired.trajectory_key
    assert (await world.gauges(repaired.id))["gauge_total_tool_calls"] == 4
    await world.deps.store.move_cursor(world.agent.id, since)
    assert (await world.run_pass(after_seconds=240)).stored == 0


class FlakyBlobStore(DictBlobStore):
    def __init__(self) -> None:
        super().__init__()
        self.refuse: set[str] = set()

    async def put_object(self, key: str, data: bytes) -> None:
        if any(run_id in key for run_id in self.refuse):
            raise OSError(f"disk full writing {key}")
        await super().put_object(key, data)


async def test_one_session_that_cannot_be_stored_is_recorded_and_the_rest_are_stored(
    app_run_world,
):
    world = app_run_world
    blob = FlakyBlobStore()
    blob.refuse = {"chat-2"}
    world.deps = dataclasses.replace(world.deps, blob_store=blob)
    for index, trace in enumerate("abc", start=1):
        await world.spans.upsert(world.agent.id, session_spans(f"chat-{index}", trace))
    since = await world.cursor(world.agent.id)
    result = await world.run_pass(after_seconds=60)
    assert (result.stored, result.failed, result.stopped) == (2, 1, None)
    runs = {run.harness_run_id: run for run in await world.runs()}
    assert [runs[f"chat-{n}"].trajectory_status for n in (1, 2, 3)] == [
        "valid",
        "unavailable",
        "valid",
    ]
    assert runs["chat-2"].trajectory_note == (
        "evalkit could not store this run: OSError: disk full writing "
        f"trajectories/{world.agent.id}/chat-2.json"
    )
    assert await world.cursor(world.agent.id) > since
    blob.refuse = set()
    await world.deps.store.move_cursor(world.agent.id, since)
    repaired = await world.run_pass(after_seconds=120)
    assert (repaired.stored, repaired.failed) == (1, 0)
    assert {run.trajectory_status for run in await world.runs()} == {"valid"}


async def test_three_failures_in_a_row_stop_the_pass_and_the_next_pass_tries_again(
    app_run_world,
):
    world = app_run_world
    blob = FlakyBlobStore()
    blob.refuse = {"chat-"}
    world.deps = dataclasses.replace(world.deps, blob_store=blob)
    for index, trace in enumerate("abcd", start=1):
        await world.spans.upsert(world.agent.id, session_spans(f"chat-{index}", trace))
    since = await world.cursor(world.agent.id)
    result = await world.run_pass(after_seconds=60)
    assert (result.stored, result.failed, result.stopped) == (0, 3, TOO_MANY_FAILURES)
    assert await world.cursor(world.agent.id) == since
    assert [run.trajectory_status for run in await world.runs()] == ["unavailable"] * 3
    blob.refuse = set()
    again = await world.run_pass(after_seconds=120)
    assert (again.stored, again.failed, again.stopped) == (4, 0, None)
    assert {run.trajectory_status for run in await world.runs()} == {"valid"}


async def test_three_sessions_that_never_store_do_not_hold_the_sessions_after_them(
    app_run_world,
):
    world = app_run_world
    blob = FlakyBlobStore()
    blob.refuse = {"chat-1", "chat-2", "chat-3"}
    world.deps = dataclasses.replace(world.deps, blob_store=blob)
    for index, trace in enumerate("abcd", start=1):
        await world.spans.upsert(world.agent.id, session_spans(f"chat-{index}", trace))
    since = await world.cursor(world.agent.id)
    for seconds in (60, 120):
        result = await world.run_pass(after_seconds=seconds)
        assert (result.stored, result.failed, result.stopped) == (0, 3, TOO_MANY_FAILURES)
    assert [run.trajectory_status for run in await world.runs()] == ["invalid"] * 3
    third = await world.run_pass(after_seconds=180)
    assert (third.stored, third.failed, third.stopped) == (1, 0, None)
    runs = {run.harness_run_id: run.trajectory_status for run in await world.runs()}
    assert runs["chat-4"] == "valid"
    assert await world.cursor(world.agent.id) > since
