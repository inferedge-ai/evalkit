import dataclasses
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from sqlalchemy import select

from evalkit.config import OtelConfig
from evalkit.contracts.enums import TrajectorySource
from evalkit.core.agents import AgentDefinition
from evalkit.core.app_runs import HARNESS_RETRY, AppRunDeps, store_app_runs
from evalkit.core.harness import HarnessClient
from evalkit.core.retry import RetryTransport
from evalkit.core.traces.raw import RawSpan
from evalkit.db.agents import AgentStore
from evalkit.db.app_runs import PgAppRunStore
from evalkit.db.mappings import MappingStore
from evalkit.db.models import AgentRun, Evaluation
from evalkit.db.sessions import DbSessionReader
from evalkit.db.spans import SpanStore
from tests.db.test_sessions import realistic_session
from tests.fake_agents import reference_mapping
from tests.fake_blob import DictBlobStore
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent, seed_identity, workspace_ref

SETTLE_SECONDS = 10
FAST_RETRY = dataclasses.replace(HARNESS_RETRY, backoff_factor=0.0)


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


class AppRunWorld:
    def __init__(self, factory, agent, spans, deps):
        self.factory = factory
        self.agent = agent
        self.spans = spans
        self.deps = deps
        self.at = datetime.now(UTC)
        self.harness = FakeHarness()
        self.imports: list[tuple[str, datetime, datetime]] = []
        self.harness_agent: AgentDefinition | None = None

    async def run_pass(self, after_seconds: int):
        self.at = datetime.now(UTC) + timedelta(seconds=after_seconds)
        return await store_app_runs(self.deps, self.agent)

    async def harness_client(self, agent: AgentDefinition) -> HarnessClient:
        transport = httpx2.ASGITransport(app=self.harness.app)
        return HarnessClient(
            agent, self.harness.api_key, transport=RetryTransport(transport, FAST_RETRY)
        )

    async def import_traces(self, agent: AgentDefinition, cursor: datetime, now: datetime) -> None:
        self.imports.append((agent.ref, cursor, now))

    async def harness_pass(self, days_later: int = 0):
        self.at = datetime.now(UTC) + timedelta(days=days_later)
        return await store_app_runs(self.deps, self.harness_agent)

    def add_run(self, run_id: str, minutes_ago: int, **run) -> None:
        created = datetime.now(UTC) - timedelta(minutes=minutes_ago)
        self.harness.runs[run_id] = {"polls": 0, "stopped": False, "created_at": created, **run}

    async def cursor(self, agent_id: int) -> datetime:
        return await self.deps.store.cursor(agent_id)

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
async def app_run_world(session_factory) -> AppRunWorld:
    await seed_identity(
        session_factory, {"admin": "password", "akshat": "password"}, shared=("dev",)
    )
    agents = AgentStore(session_factory, "default")
    agent = await agents.create(
        await workspace_ref(session_factory, "akshat"),
        "support-bot",
        trajectory_source=TrajectorySource.OTEL,
        mapping=reference_mapping(),
    )
    spans = SpanStore(session_factory, "default")
    reader = DbSessionReader(agents, spans, MappingStore(session_factory, "default"), OtelConfig())
    built = AppRunWorld(session_factory, agent, spans, None)
    built.harness_agent = await seed_agent(session_factory, "harness-bot", "dev")
    built.deps = AppRunDeps(
        store=PgAppRunStore(session_factory, "default"),
        sessions=reader,
        blob_store=DictBlobStore(),
        cost_map={},
        settle_seconds=lambda _: SETTLE_SECONDS,
        harness=built.harness_client,
        import_traces=built.import_traces,
        now=lambda: built.at,
    )
    return built
