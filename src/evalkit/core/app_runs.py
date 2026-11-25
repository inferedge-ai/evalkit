from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx2
from pydantic import BaseModel

from evalkit.contracts.atif import TrajectoryValidationError, validate_trajectory
from evalkit.contracts.enums import RunStatus, ScoredOn, TrajectoryStatus
from evalkit.contracts.harness import Capabilities, RunListItem
from evalkit.core.agent_runs import build_agent_run
from evalkit.core.agents import AgentDefinition
from evalkit.core.blob import BlobStore
from evalkit.core.capabilities import sdk_capabilities
from evalkit.core.harness import HarnessClient
from evalkit.core.pull import MAX_HARNESS_SCAN, list_harness_runs
from evalkit.core.retry import Retry
from evalkit.core.run_digest import Turn, clip, turns
from evalkit.core.scoring import build_context, run_gauges
from evalkit.core.traces.sessions import SessionReader, SessionTooLarge
from evalkit.core.traces.settle import is_settled_since
from evalkit.store.base import AgentRunRecord, EvaluationRow

TITLE_CHARS = 200
SKIPPED_GAUGES = frozenset({"gauge_judge_cost_usd"})
HARNESS_RUNS_PER_PASS = 100
NO_RUN_LISTING = "the harness does not list its runs (provides.run_listing)"
HARNESS_GIVE_UP_AFTER = timedelta(hours=24)
HARNESS_FINAL = frozenset({"completed", "failed", "cancelled"})
HARNESS_RETRY = Retry(
    total=2,
    status_forcelist=(429, 500, 502, 503, 504),
    retry_on_exceptions=(httpx2.NetworkError, httpx2.TimeoutException),
    backoff_factor=1.0,
    max_backoff_wait=30.0,
)


class AppSession(BaseModel):
    session_id: str
    started_at: datetime
    ended_at: datetime | None
    last_received_at: datetime
    failed: bool
    title: str | None
    from_sdk_run: bool


class SavedRun(BaseModel):
    stored: bool
    replaced_trajectory_key: str | None = None


class AppRunStore(Protocol):
    async def cursor(self, agent_id: int) -> datetime: ...

    async def move_cursor(self, agent_id: int, to: datetime) -> None: ...

    async def list_app_sessions(
        self, agent_id: int, received_after: datetime
    ) -> list[AppSession]: ...

    async def stored_run_ids(self, agent_id: int, run_ids: list[str]) -> set[str]: ...

    async def eval_run_ids(self, agent_id: int, run_ids: list[str]) -> set[str]: ...

    async def save(
        self, agent_id: int, run: AgentRunRecord, rows: list[EvaluationRow]
    ) -> SavedRun: ...


@dataclass(frozen=True)
class AppRunDeps:
    store: AppRunStore
    sessions: SessionReader
    blob_store: BlobStore
    cost_map: dict[str, Any]
    settle_seconds: Callable[[AgentDefinition], int]
    harness: Callable[[AgentDefinition], Awaitable[HarnessClient]]
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


class PassResult(BaseModel):
    stored: int
    waiting: int
    stopped: str | None = None


def _wall_ms(session: AppSession) -> int | None:
    if session.ended_at is None:
        return None
    return int((session.ended_at - session.started_at).total_seconds() * 1000)


def first_question(run_turns: list[Turn]) -> str | None:
    return next((t.question for t in run_turns if t.question), None)


async def build_app_run(
    deps: AppRunDeps, agent: AgentDefinition, session: AppSession
) -> AgentRunRecord:
    status = RunStatus.FAILED if session.failed else RunStatus.COMPLETED
    trajectory = None
    note: str | None = None
    # A session that cannot be read as ATIF is
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
        title=clip(first_question(run_turns), TITLE_CHARS) or clip(session.title, TITLE_CHARS),
    )


async def score_app_run(
    deps: AppRunDeps, agent: AgentDefinition, run: AgentRunRecord, caps: Capabilities
) -> list[EvaluationRow]:
    ctx, _ = await build_context(deps.blob_store, agent, deps.cost_map, run)
    rows = await run_gauges(ctx, caps, SKIPPED_GAUGES)
    return [row.model_copy(update={"scored_on": ScoredOn.SERVER}) for row in rows]


async def save_app_run(
    deps: AppRunDeps, agent: AgentDefinition, run: AgentRunRecord, caps: Capabilities
) -> bool:
    saved = await deps.store.save(agent.id, run, await score_app_run(deps, agent, run, caps))
    if saved.replaced_trajectory_key and saved.replaced_trajectory_key != run.trajectory_key:
        await deps.blob_store.delete_object(saved.replaced_trajectory_key)
    return saved.stored


async def store_app_runs(deps: AppRunDeps, agent: AgentDefinition) -> PassResult:
    if agent.is_otel:
        return await store_session_runs(deps, agent)
    return await store_harness_runs(deps, agent)


async def store_session_runs(deps: AppRunDeps, agent: AgentDefinition) -> PassResult:
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
            stored += await save_app_run(deps, agent, run, sdk_capabilities(agent))
        cursor = session.last_received_at
    await deps.store.move_cursor(agent.id, cursor)
    return PassResult(stored=stored, waiting=waiting)


def is_missing(exc: httpx2.HTTPStatusError) -> bool:
    return exc.response.status_code == 404


async def build_harness_run(
    deps: AppRunDeps, agent: AgentDefinition, client: HarnessClient, item: RunListItem
) -> AgentRunRecord | None:
    assert item.created_at is not None
    # Spec "TB2, Rules": a run the harness no longer has, or whose trajectory
    # is not ATIF, is stored as unavailable; any other failure stops the pass.
    try:
        status = await client.status(item.run_id)
    except httpx2.HTTPStatusError as exc:
        if not is_missing(exc):
            raise
        return await build_agent_run(
            deps.blob_store,
            deps.cost_map,
            agent.id,
            harness_run_id=item.run_id,
            status=RunStatus.FAILED,
            started_at=item.created_at,
            ended_at=None,
            trajectory=None,
            trajectory_status=TrajectoryStatus.UNAVAILABLE,
            trajectory_note=str(exc),
            error=str(exc),
            title=clip(item.title, TITLE_CHARS),
            session_id=item.session_id,
        )
    if status.status not in HARNESS_FINAL:
        return None
    trajectory = None
    trajectory_status = TrajectoryStatus.VALID
    note: str | None = None
    try:
        trajectory = await client.trajectory(item.run_id)
    except httpx2.HTTPStatusError as exc:
        if not is_missing(exc):
            raise
        trajectory_status, note = TrajectoryStatus.UNAVAILABLE, str(exc)
    except TrajectoryValidationError as exc:
        trajectory_status, note = TrajectoryStatus.INVALID, str(exc)
    run_turns = turns(trajectory) if trajectory is not None else []
    ended = max((t.ended_at for t in run_turns if t.ended_at is not None), default=None)
    return await build_agent_run(
        deps.blob_store,
        deps.cost_map,
        agent.id,
        harness_run_id=item.run_id,
        status=RunStatus(status.status),
        started_at=item.created_at,
        ended_at=ended,
        trajectory=trajectory,
        trajectory_status=trajectory_status,
        trajectory_note=note,
        answer=status.answer,
        output=status.output,
        error=status.error,
        wall_ms=int((ended - item.created_at).total_seconds() * 1000) if ended else None,
        title=clip(item.title, TITLE_CHARS) or clip(first_question(run_turns), TITLE_CHARS),
        model=status.model,
        session_id=item.session_id,
    )


async def store_harness_runs(deps: AppRunDeps, agent: AgentDefinition) -> PassResult:
    now = deps.now()
    cursor = await deps.store.cursor(agent.id)
    stored = waiting = 0
    hold: datetime | None = None
    stopped: str | None = None
    client = await deps.harness(agent)

    async def eval_runs(run_ids: list[str]) -> set[str]:
        return await deps.store.eval_run_ids(agent.id, run_ids)

    # Spec "TB2, Rules": when the harness keeps failing after its retries the
    # pass stops, keeps the cursor before the run it failed on, and the next
    # scheduled pass tries again.
    try:
        caps = await client.capabilities()
        if not caps.provides.run_listing:
            return PassResult(stored=0, waiting=0, stopped=NO_RUN_LISTING)
        listed = await list_harness_runs(
            client, wanted=MAX_HARNESS_SCAN, since=cursor, exclude=eval_runs
        )
        dated = [item for item in listed.runs if item.created_at is not None]
        runs = sorted(dated, key=lambda item: item.created_at or now)
        known = await deps.store.stored_run_ids(agent.id, [item.run_id for item in runs])
        for item in runs:
            assert item.created_at is not None
            if item.run_id not in known:
                if stored == HARNESS_RUNS_PER_PASS:
                    break
                run = await build_harness_run(deps, agent, client, item)
                if run is None and item.created_at > now - HARNESS_GIVE_UP_AFTER:
                    waiting += 1
                    hold = hold or item.created_at
                    continue
                if run is not None:
                    stored += await save_app_run(deps, agent, run, caps)
            cursor = item.created_at
    except httpx2.HTTPError as exc:
        stopped = str(exc)
    finally:
        await client.aclose()
    await deps.store.move_cursor(agent.id, min(hold, cursor) if hold else cursor)
    return PassResult(stored=stored, waiting=waiting, stopped=stopped)
