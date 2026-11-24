from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel

from evalkit.contracts.atif import TrajectoryValidationError, validate_trajectory
from evalkit.contracts.enums import RunStatus, ScoredOn, TrajectoryStatus
from evalkit.core.agent_runs import build_agent_run
from evalkit.core.agents import AgentDefinition
from evalkit.core.blob import BlobStore
from evalkit.core.capabilities import sdk_capabilities
from evalkit.core.run_digest import clip, turns
from evalkit.core.scoring import build_context, run_gauges
from evalkit.core.traces.sessions import SessionReader, SessionTooLarge
from evalkit.core.traces.settle import is_settled_since
from evalkit.store.base import AgentRunRecord, EvaluationRow

TITLE_CHARS = 200
SKIPPED_GAUGES = frozenset({"gauge_judge_cost_usd"})


class AppSession(BaseModel):
    session_id: str
    started_at: datetime
    ended_at: datetime | None
    last_received_at: datetime
    failed: bool
    title: str | None
    from_sdk_run: bool


class AppRunStore(Protocol):
    async def cursor(self, agent_id: int) -> datetime: ...

    async def move_cursor(self, agent_id: int, to: datetime) -> None: ...

    async def list_app_sessions(
        self, agent_id: int, received_after: datetime
    ) -> list[AppSession]: ...

    async def stored_run_ids(self, agent_id: int, run_ids: list[str]) -> set[str]: ...

    async def save(self, agent_id: int, run: AgentRunRecord, rows: list[EvaluationRow]) -> bool: ...


@dataclass(frozen=True)
class AppRunDeps:
    store: AppRunStore
    sessions: SessionReader
    blob_store: BlobStore
    cost_map: dict[str, Any]
    settle_seconds: Callable[[AgentDefinition], int]
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


class PassResult(BaseModel):
    stored: int
    waiting: int


def _wall_ms(session: AppSession) -> int | None:
    if session.ended_at is None:
        return None
    return int((session.ended_at - session.started_at).total_seconds() * 1000)


async def build_app_run(
    deps: AppRunDeps, agent: AgentDefinition, session: AppSession
) -> AgentRunRecord:
    status = RunStatus.FAILED if session.failed else RunStatus.COMPLETED
    trajectory = None
    note: str | None = None
    # Spec "Decided for later TBs": a session that cannot be read as ATIF is
    # stored as unavailable, so one bad session never blocks an agent's pass.
    try:
        result = await deps.sessions.trajectory(agent.ref, session.session_id)
        if result.trajectory is None or not result.trajectory.get("steps"):
            note = result.reason or "the session has no steps"
        else:
            trajectory = validate_trajectory(result.trajectory)
    except (SessionTooLarge, TrajectoryValidationError) as exc:
        note = str(exc)
    run_turns = turns(trajectory) if trajectory is not None else []
    answer = next((t.answer for t in reversed(run_turns) if t.answer), None)
    question = next((t.question for t in run_turns if t.question), None)
    return await build_agent_run(
        deps.blob_store,
        deps.cost_map,
        agent.id,
        harness_run_id=session.session_id,
        status=status,
        started_at=session.started_at,
        ended_at=session.ended_at,
        trajectory=trajectory,
        trajectory_status=(
            TrajectoryStatus.VALID if trajectory is not None else TrajectoryStatus.UNAVAILABLE
        ),
        trajectory_note=note,
        answer=answer,
        wall_ms=_wall_ms(session),
        title=clip(question, TITLE_CHARS) or clip(session.title, TITLE_CHARS),
    )


async def score_app_run(
    deps: AppRunDeps, agent: AgentDefinition, run: AgentRunRecord
) -> list[EvaluationRow]:
    ctx, _ = await build_context(deps.blob_store, agent, deps.cost_map, run)
    rows = await run_gauges(ctx, sdk_capabilities(agent), SKIPPED_GAUGES)
    return [row.model_copy(update={"scored_on": ScoredOn.SERVER}) for row in rows]


async def store_app_runs(deps: AppRunDeps, agent: AgentDefinition) -> PassResult:
    now = deps.now()
    settle = deps.settle_seconds(agent)
    cursor = await deps.store.cursor(agent.id)
    sessions = sorted(
        await deps.store.list_app_sessions(agent.id, cursor), key=lambda s: s.last_received_at
    )
    known = await deps.store.stored_run_ids(agent.id, [s.session_id for s in sessions])
    stored = waiting = 0
    # Spans of one export batch share a received_at, so the cursor moves once,
    # after every session up to it is handled; a pass that dies starts over and
    # skips what it already stored.
    for session in sessions:
        if not is_settled_since(session.last_received_at, now, settle):
            waiting += 1
            continue
        if not session.from_sdk_run and session.session_id not in known:
            run = await build_app_run(deps, agent, session)
            stored += await deps.store.save(agent.id, run, await score_app_run(deps, agent, run))
        cursor = session.last_received_at
    await deps.store.move_cursor(agent.id, cursor)
    return PassResult(stored=stored, waiting=waiting)
