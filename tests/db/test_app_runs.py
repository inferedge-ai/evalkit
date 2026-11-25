from datetime import UTC, datetime

from evalkit.contracts.enums import RunKind, RunStatus
from evalkit.core.traces.mapping import SDK_ROOT_ATTRIBUTE
from evalkit.db.runs import insert_agent_run
from evalkit.store.base import AgentRunRecord
from tests.app_run_world import session_spans

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
