from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2

from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sources.base import (
    BadTraceFilter,
    RateLimited,
    TracePage,
    UnknownProject,
)

OPENINFERENCE_KIND = "openinference.span.kind"
TRACE_FILTER_MIN_VERSION = (20, 12)
# Without a start time Phoenix runs the filter over the whole project, which on a
# large one takes minutes.
FILTER_PROBE_WINDOW = timedelta(hours=1)


def flatten_attributes(nested: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in nested.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(flatten_attributes(value, f"{name}."))
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            for i, item in enumerate(value):
                flat.update(flatten_attributes(item, f"{name}.{i}."))
        else:
            flat[name] = value
    return flat


def _with_span_kind(attributes: dict[str, Any], span: dict[str, Any]) -> dict[str, Any]:
    kind = span.get("span_kind")
    if kind and kind != "UNKNOWN" and OPENINFERENCE_KIND not in attributes:
        return {**attributes, OPENINFERENCE_KIND: kind}
    return attributes


def _to_raw(span: dict[str, Any]) -> RawSpan:
    context = span.get("context") or {}
    end = span.get("end_time")
    return RawSpan(
        trace_id=str(context.get("trace_id") or span.get("trace_id")),
        span_id=str(context.get("span_id") or span.get("span_id")),
        parent_span_id=span.get("parent_id") or None,
        name=str(span.get("name") or ""),
        kind=str(span.get("span_kind") or "INTERNAL"),
        start_time=datetime.fromisoformat(span["start_time"]),
        end_time=datetime.fromisoformat(end) if end else None,
        status_code=str(span.get("status_code") or "UNSET"),
        status_message=span.get("status_message") or None,
        attributes=_with_span_kind(flatten_attributes(span.get("attributes") or {}), span),
        events=[
            {
                "name": e.get("name"),
                "time": e.get("timestamp"),
                "attributes": e.get("attributes") or {},
            }
            for e in span.get("events") or []
        ],
        resource={},
    )


SPAN_PAGE = 1000


class PhoenixClient:
    def __init__(
        self,
        url: str,
        api_key: str | None,
        page_traces: int,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 120.0,
    ):
        self._url = url.rstrip("/")
        self._timeout = timeout
        self._api_key = api_key
        self._page_traces = page_traces
        self._transport = transport

    def _client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url=self._url,
            headers={"Authorization": f"Bearer {self._api_key}"} if self._api_key else {},
            timeout=self._timeout,
            transport=self._transport,
        )

    async def check_project(self, project: str) -> None:
        async with self._client() as client:
            resp = await client.get(f"/v1/projects/{project}")
        if resp.status_code == 404:
            raise UnknownProject(f"project {project} not found")
        resp.raise_for_status()

    async def check_filter(self, project: str, trace_filter: str | None) -> None:
        if not trace_filter:
            return
        async with self._client() as client:
            version = await client.get("/arize_phoenix_version")
            version.raise_for_status()
            running = version.text.strip()
            major, minor = running.split(".")[:2]
            if (int(major), int(minor)) < TRACE_FILTER_MIN_VERSION:
                raise BadTraceFilter(
                    "Phoenix trace filters need Phoenix 20.12.0 or later; this server runs "
                    f"{running}, which ignores the filter"
                )
            resp = await client.get(
                f"/v1/projects/{project}/traces",
                params={
                    "filter": trace_filter,
                    "limit": "1",
                    "start_time": (datetime.now(UTC) - FILTER_PROBE_WINDOW).isoformat(),
                },
            )
        if resp.status_code in (400, 422):
            raise BadTraceFilter(f"Phoenix refused the trace filter: {resp.text}")
        resp.raise_for_status()

    async def _pages(
        self, path: str, params: dict[str, str | list[str]]
    ) -> AsyncIterator[list[dict[str, Any]]]:
        cursor: str | None = None
        async with self._client() as client:
            while True:
                resp = await client.get(
                    path, params={**params, **({"cursor": cursor} if cursor else {})}
                )
                if resp.status_code == 429:
                    raise RateLimited(resp)
                resp.raise_for_status()
                body = resp.json()
                rows = body.get("data") or []
                if rows:
                    yield rows
                cursor = body.get("next_cursor")
                if not cursor or not rows:
                    return

    def _traces(
        self, project: str, trace_filter: str | None, params: dict[str, str]
    ) -> AsyncIterator[list[dict[str, Any]]]:
        return self._pages(
            f"/v1/projects/{project}/traces",
            {
                "sort": "start_time",
                "order": "asc",
                "limit": str(self._page_traces),
                **({"filter": trace_filter} if trace_filter else {}),
                **params,
            },
        )

    async def trace_spans(self, project: str, trace_ids: list[str]) -> AsyncIterator[list[RawSpan]]:
        params: dict[str, str | list[str]] = {"trace_id": trace_ids, "limit": str(SPAN_PAGE)}
        async for spans in self._pages(f"/v1/projects/{project}/spans", params):
            yield [_to_raw(span) for span in spans]

    async def session_spans(
        self, project: str, trace_filter: str | None, session_id: str
    ) -> list[RawSpan]:
        found: list[RawSpan] = []
        session = {"session_identifier": session_id}
        async for traces in self._traces(project, trace_filter, session):
            async for spans in self.trace_spans(project, [t["trace_id"] for t in traces]):
                found.extend(spans)
        return found

    async def trace_pages(
        self, project: str, trace_filter: str | None, since: datetime, until: datetime
    ) -> AsyncIterator[TracePage]:
        window = {"start_time": since.isoformat(), "end_time": until.isoformat()}
        async for traces in self._traces(project, trace_filter, window):
            yield TracePage(
                traces=[
                    (str(trace["trace_id"]), datetime.fromisoformat(trace["start_time"]))
                    for trace in traces
                ]
            )
