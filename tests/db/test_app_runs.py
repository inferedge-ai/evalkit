from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from evalkit.config import OtelConfig
from evalkit.contracts.enums import RunKind, RunStatus, TrajectorySource
from evalkit.core.app_runs import AppRunDeps, store_app_runs
from evalkit.core.traces.mapping import SDK_ROOT_ATTRIBUTE
from evalkit.core.traces.raw import RawSpan
from evalkit.db.agents import AgentStore
from evalkit.db.app_runs import PgAppRunStore, agents_with_app_runs
from evalkit.db.mappings import MappingStore
from evalkit.db.models import AgentRun, Evaluation
from evalkit.db.runs import insert_agent_run
from evalkit.db.sessions import DbSessionReader
from evalkit.db.spans import SpanStore
from evalkit.store.base import AgentRunRecord
from tests.db.test_sessions import realistic_session
from tests.fake_agents import reference_mapping
from tests.fake_blob import DictBlobStore
from tests.identity import seed_identity, workspace_ref

SETTLE_SECONDS = 10
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


def session_spans(session_id: str, trace: str) -> list[RawSpan]:
    return [
        span.model_copy(
            update={
                "trace_id": trace * 32,
                "session_id_hint": session_id if span.parent_span_id is None else None,
            }
        )
        for span in realistic_session(turns=3, chunk=20)
    ]


class World:
    def __init__(self, factory, agent, spans, deps):
        self.factory = factory
        self.agent = agent
        self.spans = spans
        self.deps = deps
        self.at = datetime.now(UTC)

    async def run_pass(self, after_seconds: int):
        self.at = datetime.now(UTC) + timedelta(seconds=after_seconds)
        return await store_app_runs(self.deps, self.agent)

    async def runs(self) -> list[AgentRun]:
        async with self.factory() as s:
            return list((await s.scalars(select(AgentRun).order_by(AgentRun.id))).all())

    async def gauges(self, agent_run_id: int) -> dict[str, float | None]:
        async with self.factory() as s:
            rows = await s.scalars(
                select(Evaluation).where(Evaluation.agent_run_id == agent_run_id)
            )
            return {row.name: row.value for row in rows.all()}


@pytest.fixture
async def world(session_factory) -> World:
    await seed_identity(session_factory, {"admin": "password", "akshat": "password"}, shared=())
    agents = AgentStore(session_factory, "default")
    agent = await agents.create(
        await workspace_ref(session_factory, "akshat"),
        "support-bot",
        trajectory_source=TrajectorySource.OTEL,
        mapping=reference_mapping(),
    )
    spans = SpanStore(session_factory, "default")
    reader = DbSessionReader(agents, spans, MappingStore(session_factory, "default"), OtelConfig())
    built = World(session_factory, agent, spans, None)
    built.deps = AppRunDeps(
        store=PgAppRunStore(session_factory, "default"),
        sessions=reader,
        blob_store=DictBlobStore(),
        cost_map={},
        settle_seconds=lambda _: SETTLE_SECONDS,
        now=lambda: built.at,
    )
    return built


async def test_a_settled_session_is_stored_once_with_its_gauges(world):
    await world.spans.upsert(world.agent.id, session_spans("chat-1", "a"))
    first = await world.run_pass(after_seconds=60)
    again = await world.run_pass(after_seconds=120)
    assert (first.stored, again.stored) == (1, 0)
    [run] = await world.runs()
    assert (run.kind, run.harness_run_id, run.status) == ("app", "chat-1", "completed")
    assert run.trajectory_status == "valid" and run.trajectory_key
    assert run.answer == "main step 2" and run.title == "main question"
    gauges = await world.gauges(run.id)
    assert set(gauges) == GAUGES
    assert gauges["gauge_total_tool_calls"] == 4 and gauges["gauge_input_tokens"]


async def test_an_unsettled_session_waits_for_a_later_pass(world):
    await world.spans.upsert(world.agent.id, session_spans("chat-1", "a"))
    early = await world.run_pass(after_seconds=1)
    assert (early.stored, early.waiting) == (0, 1)
    assert await world.runs() == []
    later = await world.run_pass(after_seconds=60)
    assert later.stored == 1


async def test_a_session_that_arrives_after_a_pass_is_stored_by_the_next(world):
    await world.spans.upsert(world.agent.id, session_spans("chat-1", "a"))
    await world.run_pass(after_seconds=60)
    await world.spans.upsert(world.agent.id, session_spans("chat-2", "b"))
    second = await world.run_pass(after_seconds=120)
    assert second.stored == 1
    assert [run.harness_run_id for run in await world.runs()] == ["chat-1", "chat-2"]


async def test_eval_sessions_are_not_stored_as_app_runs(world):
    await world.spans.upsert(world.agent.id, session_spans("eval-1", "a"))
    sdk = session_spans("sdk-1", "b")
    sdk[0] = sdk[0].model_copy(
        update={"attributes": {**sdk[0].attributes, SDK_ROOT_ATTRIBUTE: True}}
    )
    await world.spans.upsert(world.agent.id, sdk)
    async with world.factory() as s:
        await insert_agent_run(
            s,
            world.agent.id,
            AgentRunRecord(
                harness_run_id="eval-1", status=RunStatus.COMPLETED, started_at=datetime.now(UTC)
            ),
            RunKind.EVAL,
        )
        await s.commit()
    result = await world.run_pass(after_seconds=60)
    assert result.stored == 0
    assert [run.kind for run in await world.runs()] == ["eval"]


async def test_a_session_with_no_steps_is_stored_as_unavailable(world):
    root = session_spans("empty-1", "a")[0]
    await world.spans.upsert(world.agent.id, [root])
    result = await world.run_pass(after_seconds=60)
    [run] = await world.runs()
    assert result.stored == 1
    assert run.trajectory_status == "unavailable" and run.trajectory_note


async def test_only_unarchived_otel_agents_have_app_runs_stored(world, session_factory):
    await AgentStore(session_factory, "default").create(
        await workspace_ref(session_factory, "akshat"), "harness-bot", harness_url="http://h"
    )
    assert await agents_with_app_runs(session_factory) == [("default", world.agent.id)]
