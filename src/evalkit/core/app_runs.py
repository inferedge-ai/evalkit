from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import httpx2
from pydantic import BaseModel

from evalkit.contracts.atif import TrajectoryValidationError, validate_trajectory
from evalkit.contracts.enums import RunStatus, ScoredOn, TrajectoryStatus
from evalkit.contracts.harness import Capabilities, RunListItem
from evalkit.core.agent_runs import build_agent_run, shorten_title
from evalkit.core.agents import AgentDefinition
from evalkit.core.blob import BlobStore
from evalkit.core.capabilities import sdk_capabilities
from evalkit.core.errors import error_text
from evalkit.core.harness import HarnessClient
from evalkit.core.pull import MAX_HARNESS_SCAN, list_harness_runs
from evalkit.core.retry import Retry
from evalkit.core.run_digest import Turn, turns
from evalkit.core.scoring import build_context, run_gauges
from evalkit.core.traces.sessions import SessionReader, SessionTooLarge
from evalkit.core.traces.settle import is_settled_since
from evalkit.store.base import AgentRunRecord, EvaluationRow

LOGGER = logging.getLogger(__name__)

SKIPPED_GAUGES = frozenset({"gauge_judge_cost_usd"})
NO_RUN_LISTING = "the harness does not list its runs (provides.run_listing)"
MAX_FAILURES_IN_A_ROW = 3
TOO_MANY_FAILURES = (
    f"{MAX_FAILURES_IN_A_ROW} runs in a row could not be stored; "
    "the next pass tries each of them once more"
)
HARNESS_GIVE_UP_AFTER = timedelta(hours=24)
HARNESS_WINDOW = timedelta(hours=6)
HARNESS_MIN_WINDOW = timedelta(minutes=1)
HARNESS_CLOCK_MARGIN = timedelta(hours=1)
TOO_MANY_RUNS = (
    f"the harness lists more than {MAX_HARNESS_SCAN} runs created within one minute; "
    "none of them was stored"
)
IGNORES_UNTIL = (
    f"the harness ignores until: GET /runs returned more than {MAX_HARNESS_SCAN} runs, some "
    "created at or after it; none of them was stored"
)
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
    replaced_unread_run: bool = False
    older_than_stored: bool = False


class AppRunStore(Protocol):
    async def cursor(self, agent_id: int) -> datetime: ...

    async def move_cursor(self, agent_id: int, to: datetime) -> None: ...

    async def list_app_sessions(
        self, agent_id: int, received_after: datetime
    ) -> list[AppSession]: ...

    async def stored_run_ids(self, agent_id: int, run_ids: list[str]) -> set[str]: ...

    async def stored_session_times(
        self, agent_id: int, session_ids: list[str]
    ) -> dict[str, datetime]: ...

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
    import_traces: Callable[[AgentDefinition, datetime, datetime], Awaitable[None]]
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


class PassResult(BaseModel):
    stored: int
    waiting: int
    failed: int = 0
    stopped: str | None = None


@dataclass
class Failures:
    total: int = 0
    in_a_row: int = 0

    def reset(self) -> None:
        self.in_a_row = 0

    def add(self) -> bool:
        self.total += 1
        self.in_a_row += 1
        return self.in_a_row >= MAX_FAILURES_IN_A_ROW


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
            note = result.reason or (
                f"mapping v{result.mapping_version} read no LLM or tool steps from the "
                f"session's {result.span_count} spans; check the agent's mapping with "
                f"evalkit traces check {agent.ref} {session.session_id}"
            )
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
        title=shorten_title(first_question(run_turns)) or shorten_title(session.title),
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
    if saved.older_than_stored and run.trajectory_key:
        await deps.blob_store.delete_object(run.trajectory_key)
    return saved.stored


async def record_unstored_run(
    deps: AppRunDeps,
    agent: AgentDefinition,
    exc: Exception,
    *,
    run_id: str,
    status: RunStatus,
    started_at: datetime,
    title: str | None,
    session_id: str | None = None,
) -> None:
    LOGGER.error("could not store app run %s of agent %s", run_id, agent.ref, exc_info=exc)
    run = await build_agent_run(
        deps.blob_store,
        deps.cost_map,
        agent.id,
        harness_run_id=run_id,
        status=status,
        started_at=started_at,
        ended_at=None,
        trajectory=None,
        trajectory_status=TrajectoryStatus.UNAVAILABLE,
        trajectory_note=f"evalkit could not store this run: {error_text(exc)}",
        title=shorten_title(title),
        session_id=session_id,
    )
    saved = await deps.store.save(agent.id, run, [])
    # A run that could not be stored in an earlier pass either is not read a
    # third time, so runs that always fail cannot hold an agent's passes.
    if saved.replaced_unread_run:
        gave_up = run.model_copy(update={"trajectory_status": TrajectoryStatus.INVALID})
        await deps.store.save(agent.id, gave_up, [])


async def store_app_runs(deps: AppRunDeps, agent: AgentDefinition) -> PassResult:
    if agent.is_otel:
        return await store_session_runs(deps, agent)
    return await store_harness_runs(deps, agent)


async def store_session_runs(deps: AppRunDeps, agent: AgentDefinition) -> PassResult:
    now = deps.now()
    settle = deps.settle_seconds(agent)
    start = cursor = await deps.store.cursor(agent.id)
    if agent.settings.trace_source is not None:
        await deps.import_traces(agent, cursor, now)
    sessions = sorted(
        await deps.store.list_app_sessions(agent.id, cursor), key=lambda s: s.last_received_at
    )
    known = await deps.store.stored_run_ids(agent.id, [s.session_id for s in sessions])
    stored = waiting = 0
    failures = Failures()
    stopped: str | None = None
    # Spans of one export batch share a received_at, so the cursor moves once,
    # after every session up to it is handled; a pass that dies starts over and
    # skips what it already stored.
    for session in sessions:
        if not is_settled_since(session.last_received_at, now, settle):
            waiting += 1
            continue
        if not session.from_sdk_run and session.session_id not in known:
            # One session that cannot be stored must not block the agent's
            # others: it is recorded without a trajectory and the pass goes on,
            # unless several fail in a row, which points at storage, not runs.
            try:
                run = await build_app_run(deps, agent, session)
                stored += await save_app_run(deps, agent, run, sdk_capabilities(agent))
                failures.reset()
            except Exception as exc:
                await record_unstored_run(
                    deps,
                    agent,
                    exc,
                    run_id=session.session_id,
                    status=RunStatus.FAILED if session.failed else RunStatus.COMPLETED,
                    started_at=session.started_at,
                    title=session.title,
                )
                if failures.add():
                    cursor, stopped = start, TOO_MANY_FAILURES
                    break
        cursor = session.last_received_at
    await deps.store.move_cursor(agent.id, cursor)
    return PassResult(stored=stored, waiting=waiting, failed=failures.total, stopped=stopped)


def is_missing(exc: httpx2.HTTPStatusError) -> bool:
    return exc.response.status_code == 404


async def build_harness_run(
    deps: AppRunDeps, agent: AgentDefinition, client: HarnessClient, item: RunListItem
) -> AgentRunRecord | None:
    assert item.created_at is not None
    # A run the harness no longer has, or whose trajectory
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
            title=shorten_title(item.title),
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
        title=shorten_title(item.title) or shorten_title(first_question(run_turns)),
        model=status.model,
        session_id=item.session_id,
    )


def is_older_than_stored(item: RunListItem, session_times: dict[str, datetime]) -> bool:
    stored_at = session_times.get(item.session_id) if item.session_id else None
    return stored_at is not None and item.created_at is not None and item.created_at < stored_at


async def list_harness_window(
    deps: AppRunDeps, agent: AgentDefinition, client: HarnessClient, since: datetime, now: datetime
) -> tuple[list[RunListItem], datetime] | str:
    async def eval_runs(run_ids: list[str]) -> set[str]:
        return await deps.store.eval_run_ids(agent.id, run_ids)

    # The harness lists newest first and a listing stops at MAX_HARNESS_SCAN
    # runs, so a window that reaches it is halved: its oldest runs were not
    # listed, and moving the cursor past them would lose them.
    span = HARNESS_WINDOW
    while span >= HARNESS_MIN_WINDOW:
        until = min(since + span, now)
        listed = await list_harness_runs(
            client, wanted=MAX_HARNESS_SCAN + 1, since=since, until=until, exclude=eval_runs
        )
        if not listed.capped:
            dated = [item for item in listed.runs if item.created_at is not None]
            return sorted(dated, key=lambda item: item.created_at or now), until
        if listed.ignored_until:
            return IGNORES_UNTIL
        span /= 2
    return TOO_MANY_RUNS


async def store_harness_runs(deps: AppRunDeps, agent: AgentDefinition) -> PassResult:
    now = deps.now()
    start = cursor = since = await deps.store.cursor(agent.id)
    stored = waiting = 0
    failures = Failures()
    hold: datetime | None = None
    stopped: str | None = None
    full = False
    client = await deps.harness(agent)

    # When the harness cannot be reached after its retries the pass stops,
    # keeps the cursor before the run it failed on, and the next scheduled
    # pass tries again.
    try:
        caps = await client.capabilities()
        if not caps.provides.run_listing:
            return PassResult(stored=0, waiting=0, stopped=NO_RUN_LISTING)
        while since < now and not full and stopped is None:
            window = await list_harness_window(deps, agent, client, since, now)
            if isinstance(window, str):
                stopped = window
                break
            runs, until = window
            known = await deps.store.stored_run_ids(agent.id, [item.run_id for item in runs])
            # The stored app run of a session is its newest run: an older run of
            # the session that is listed again is already covered by it.
            session_times = await deps.store.stored_session_times(
                agent.id, sorted({item.session_id for item in runs if item.session_id})
            )
            for item in runs:
                assert item.created_at is not None
                if item.run_id not in known and not is_older_than_stored(item, session_times):
                    if stored == agent.settings.app_runs_per_pass:
                        full = True
                        break
                    # As for sessions: one run that cannot be stored is recorded
                    # and the pass goes on. So is one the harness answers with an
                    # error, once it is too old to wait for; until then the
                    # cursor stays at it and each pass asks again.
                    error: Exception | None = None
                    asked_too_early = False
                    try:
                        run = await build_harness_run(deps, agent, client, item)
                        if run is None and item.created_at > now - HARNESS_GIVE_UP_AFTER:
                            waiting += 1
                            hold = hold or item.created_at
                            continue
                        if run is not None:
                            stored += await save_app_run(deps, agent, run, caps)
                        failures.reset()
                    except httpx2.HTTPStatusError as exc:
                        error = exc
                        asked_too_early = item.created_at > now - HARNESS_GIVE_UP_AFTER
                    except httpx2.HTTPError:
                        raise
                    except Exception as exc:
                        error = exc
                    if error is not None:
                        if asked_too_early:
                            LOGGER.warning(
                                "the harness of agent %s could not serve app run %s, "
                                "the next pass asks again: %s",
                                agent.ref,
                                item.run_id,
                                error,
                            )
                            hold = hold or item.created_at
                        else:
                            await record_unstored_run(
                                deps,
                                agent,
                                error,
                                run_id=item.run_id,
                                status=RunStatus.FAILED,
                                started_at=item.created_at,
                                title=item.title,
                                session_id=item.session_id,
                            )
                        if failures.add():
                            cursor, hold = start, None
                            stopped = (
                                str(error)
                                if isinstance(error, httpx2.HTTPStatusError)
                                else TOO_MANY_FAILURES
                            )
                            break
                        if asked_too_early:
                            continue
                cursor = item.created_at
            # A window the pass finished is behind the cursor for good, unless it
            # is recent enough that a harness whose clock runs behind could still
            # add a run to it.
            if not full and stopped is None and until <= now - HARNESS_CLOCK_MARGIN:
                cursor = until
            since = until
    except httpx2.HTTPError as exc:
        stopped = str(exc)
    finally:
        await client.aclose()
    await deps.store.move_cursor(agent.id, min(hold, cursor) if hold else cursor)
    return PassResult(stored=stored, waiting=waiting, failed=failures.total, stopped=stopped)
