from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from evalkit.core.traces.sessions import SessionReader, SessionTooLarge

DEFAULT_POLL_INTERVAL = 5.0
LOGGER = logging.getLogger(__name__)


async def poll_trajectory(
    sessions: SessionReader,
    agent: str,
    session_id: str,
    deadline_seconds: float,
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> tuple[dict[str, Any] | None, str]:
    started = clock()
    while True:
        try:
            resp = await sessions.trajectory(agent, session_id)
        except SessionTooLarge as exc:
            LOGGER.warning("no trajectory for session %s: %s", session_id, exc)
            return None, "unavailable"
        if resp.settled and resp.root_present:
            return resp.trajectory, "valid"
        if clock() - started > deadline_seconds:
            if resp.root_present:
                return resp.trajectory, "partial"
            return None, "unavailable"
        await sleep(poll_interval)
