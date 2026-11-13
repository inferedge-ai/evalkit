from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

from evalkit.contracts.settings import AgentSettings
from evalkit.core.traces.session_import import SessionPullFailed
from evalkit.core.traces.sessions import SessionReader, SessionTooLarge, SessionTrajectory

DEFAULT_POLL_INTERVAL = 5.0
LOGGER = logging.getLogger(__name__)


COLLECTOR = "at evalkit's collector"


class PollBudget(NamedTuple):
    seconds: float
    setting: str
    origin: str


def budget_for(settings: AgentSettings, override: int | None = None) -> PollBudget:
    if settings.trace_source is not None:
        return PollBudget(
            settings.import_wait_seconds if override is None else override,
            "import_wait_seconds",
            f"in project {settings.project} of trace source {settings.trace_source}",
        )
    return PollBudget(
        settings.trajectory_wait_seconds if override is None else override,
        "trajectory_wait_seconds",
        COLLECTOR,
    )


TASK_HINT = (
    'pass metadata["session_id"] to the agent as its session id, or return the agent\'s own '
    "id with RunResult(session_id=...)"
)
HARNESS_HINT = "the harness must put the run id it returned on its spans as their session id"


def describe_not_found(session_id: str, origin: str, deadline_seconds: float, hint: str) -> str:
    return (
        f"session {session_id} not found {origin} after {deadline_seconds:g} s. The agent's spans "
        f"must carry session id {session_id}: {hint}"
    )


async def poll_trajectory(
    sessions: SessionReader,
    agent: str,
    session_id: str,
    deadline_seconds: float,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    wait_setting: str = "trajectory_wait_seconds",
    origin: str = COLLECTOR,
    hint: str = TASK_HINT,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[dict[str, Any] | None, str, str | None]:
    started = clock()
    last: SessionTrajectory | None = None
    last_error: str | None = None
    while True:
        try:
            last = await sessions.trajectory(agent, session_id)
            last_error = None
        except SessionTooLarge as exc:
            LOGGER.warning("no trajectory for session %s: %s", session_id, exc)
            return None, "unavailable", str(exc)
        except SessionPullFailed as exc:
            LOGGER.warning("pulling session %s failed: %s", session_id, exc)
            last_error = str(exc)
        if last is not None and last_error is None and last.settled:
            if last.only_run_span:
                return None, "unavailable", last.reason
            if last.root_present:
                return last.trajectory, "valid", last.reason
        if clock() - started > deadline_seconds:
            if last_error is not None and (last is None or not last.root_present):
                return None, "unavailable", last_error
            if last is not None and last.only_run_span:
                return None, "unavailable", last.reason
            arriving = f"spans still arriving after {deadline_seconds:g} s; raise {wait_setting}"
            if last is not None and last.root_present:
                return last.trajectory, "partial", last_error or arriving
            if last is not None and last.span_count > 0:
                return None, "unavailable", arriving
            return (
                None,
                "unavailable",
                describe_not_found(session_id, origin, deadline_seconds, hint),
            )
        await sleep(poll_interval)
