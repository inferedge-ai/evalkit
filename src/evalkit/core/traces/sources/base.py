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


class TraceSourceClient(Protocol):
    async def session_spans(self, project: str, session_id: str) -> list[RawSpan]: ...

    def trace_pages(
        self, project: str, since: datetime, until: datetime
    ) -> AsyncIterator[TracePage]: ...

    def trace_spans(self, project: str, trace_ids: list[str]) -> AsyncIterator[list[RawSpan]]: ...
