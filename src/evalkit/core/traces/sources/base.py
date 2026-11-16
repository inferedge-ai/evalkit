from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

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


class TraceSourceClient(Protocol):
    async def check_project(self, project: str) -> None: ...

    def check_filter(self, trace_filter: str | None) -> None: ...

    async def session_spans(
        self, project: str, trace_filter: str | None, session_id: str
    ) -> list[RawSpan]: ...

    def trace_pages(
        self, project: str, trace_filter: str | None, since: datetime, until: datetime
    ) -> AsyncIterator[TracePage]:
        """Oldest-first sources yield since <= start < until in ascending order; newest-first
        sources yield since <= start <= until in descending order. Pages may be empty."""
        ...

    def trace_spans(self, project: str, trace_ids: list[str]) -> AsyncIterator[list[RawSpan]]: ...
