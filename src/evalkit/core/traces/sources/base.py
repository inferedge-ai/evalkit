from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import httpx2

from evalkit.core.retry import parse_retry_after
from evalkit.core.traces.raw import RawSpan


@dataclass
class TracePage:
    traces: list[tuple[str, datetime]]

    @property
    def last_start(self) -> datetime:
        return self.traces[-1][1]


class UnknownProject(Exception):
    pass


class BadTraceFilter(ValueError):
    pass


DEFAULT_RETRY_AFTER_SECONDS = 30.0


class RateLimited(httpx2.HTTPStatusError):
    def __init__(self, response: httpx2.Response):
        super().__init__(
            f"{response.request.url.host} answered 429 Too Many Requests",
            request=response.request,
            response=response,
        )
        self.retry_after = parse_retry_after(response.headers.get("Retry-After", "")) or (
            DEFAULT_RETRY_AFTER_SECONDS
        )


class TraceSourceClient(Protocol):
    async def check_project(self, project: str) -> None: ...

    async def check_filter(self, trace_filter: str | None) -> None: ...

    async def session_spans(
        self, project: str, trace_filter: str | None, session_id: str
    ) -> list[RawSpan]: ...

    def trace_pages(
        self, project: str, trace_filter: str | None, since: datetime, until: datetime
    ) -> AsyncIterator[TracePage]:
        """Yields traces with since <= start < until, oldest first or newest first as the
        provider's newest_first says. Pages may be empty."""
        ...

    def trace_spans(self, project: str, trace_ids: list[str]) -> AsyncIterator[list[RawSpan]]: ...
