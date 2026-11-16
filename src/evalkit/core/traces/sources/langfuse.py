from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import httpx2

from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sources.base import BadTraceFilter, TracePage, UnknownProject

OBSERVATIONS = "/api/public/v2/observations"
SPAN_FIELDS = "core,basic,time,io,metadata,model,usage"
SPAN_PAGE = 1000
RESERVED_COLUMNS = frozenset({"startTime", "traceId", "sessionId", "isRootObservation"})
KEY_FORMAT = "Langfuse needs the public key and secret key joined with `:`"
ROOT = {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True}


def check_langfuse_key(api_key: str | None) -> str:
    if not api_key or api_key.count(":") != 1 or "" in api_key.split(":"):
        raise ValueError(KEY_FORMAT)
    return api_key


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="milliseconds")


def _without_empty(values: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in values.items() if v is not None and v != ""}


def to_raw(obs: dict[str, Any]) -> RawSpan:
    meta = obs.get("metadata") or {}
    attributes: dict[str, Any] = {
        k.removeprefix("attributes."): v for k, v in meta.items() if k.startswith("attributes.")
    }
    attributes |= {
        f"langfuse.observation.metadata.{k}": v
        for k, v in meta.items()
        if not k.startswith(("attributes.", "resourceAttributes.", "scope."))
    }
    usage, cost = obs.get("usageDetails"), obs.get("costDetails")
    level = obs.get("level")
    attributes |= _without_empty(
        {
            "langfuse.observation.type": str(obs.get("type") or "").lower(),
            "langfuse.observation.input": obs.get("input"),
            "langfuse.observation.output": obs.get("output"),
            "langfuse.observation.model.name": obs.get("model"),
            "langfuse.observation.usage_details": json.dumps(usage) if usage else None,
            "langfuse.observation.cost_details": json.dumps(cost) if cost else None,
            "langfuse.observation.level": level,
            "langfuse.observation.status_message": obs.get("statusMessage"),
            "langfuse.environment": obs.get("environment"),
            "session.id": obs.get("sessionId"),
            "user.id": obs.get("userId"),
        }
    )
    end = obs.get("endTime")
    return RawSpan(
        trace_id=str(obs["traceId"]),
        span_id=str(obs["id"]),
        parent_span_id=obs.get("parentObservationId") or None,
        name=str(obs.get("name") or ""),
        start_time=_time(obs["startTime"]),
        end_time=_time(end) if end else None,
        status_code="ERROR" if level == "ERROR" else "UNSET",
        status_message=obs.get("statusMessage") or None,
        attributes=attributes,
        resource={
            k.removeprefix("resourceAttributes."): v
            for k, v in meta.items()
            if k.startswith("resourceAttributes.")
        },
        scope=meta.get("scope.name"),
    )


def parse_filter(trace_filter: str | None) -> list[dict[str, Any]]:
    if not trace_filter:
        return []
    try:
        conditions = json.loads(trace_filter)
    except ValueError as exc:
        raise BadTraceFilter(f"the Langfuse trace filter is not JSON: {exc}") from exc
    if not isinstance(conditions, list) or not all(isinstance(c, dict) for c in conditions):
        raise BadTraceFilter("the Langfuse trace filter must be a JSON array of conditions")
    reserved = sorted({str(c.get("column")) for c in conditions} & RESERVED_COLUMNS)
    if reserved:
        raise BadTraceFilter(
            f"the Langfuse trace filter cannot name {', '.join(reserved)}: evalkit sets "
            "startTime, traceId, sessionId and isRootObservation itself"
        )
    return conditions


class LangfuseClient:
    def __init__(
        self,
        url: str,
        api_key: str | None,
        page_traces: int,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ):
        self._url = url.rstrip("/")
        self._auth = base64.b64encode(check_langfuse_key(api_key).encode()).decode()
        self._page_traces = page_traces
        self._transport = transport
        self._timeout = timeout

    def _client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            base_url=self._url,
            headers={"Authorization": f"Basic {self._auth}"},
            timeout=self._timeout,
            transport=self._transport,
        )

    def check_filter(self, trace_filter: str | None) -> None:
        parse_filter(trace_filter)

    async def check_project(self, project: str) -> None:
        async with self._client() as client:
            resp = await client.get("/api/public/projects")
        resp.raise_for_status()
        names = [p.get("name") for p in resp.json().get("data") or []]
        if project not in names:
            raise UnknownProject(
                f"project {project} not found; this key reads {', '.join(map(str, names))}"
            )

    async def _rows(
        self, conditions: list[dict[str, Any]], fields: str, limit: int
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params = {"filter": json.dumps(conditions), "fields": fields, "limit": str(limit)}
        cursor: str | None = None
        async with self._client() as client:
            while True:
                resp = await client.get(
                    OBSERVATIONS, params={**params, **({"cursor": cursor} if cursor else {})}
                )
                resp.raise_for_status()
                body = resp.json()
                yield body.get("data") or []
                cursor = (body.get("meta") or {}).get("cursor")
                if not cursor:
                    return

    async def trace_pages(
        self, project: str, trace_filter: str | None, since: datetime, until: datetime
    ) -> AsyncIterator[TracePage]:
        window = [
            {"type": "datetime", "column": "startTime", "operator": ">=", "value": _iso(since)},
            {
                "type": "datetime",
                "column": "startTime",
                "operator": "<",
                "value": _iso(until + timedelta(milliseconds=1)),
            },
        ]
        conditions = [*parse_filter(trace_filter), ROOT, *window]
        async for rows in self._rows(conditions, "core", self._page_traces):
            yield TracePage(traces=[(str(r["traceId"]), _time(r["startTime"])) for r in rows])

    async def trace_spans(self, project: str, trace_ids: list[str]) -> AsyncIterator[list[RawSpan]]:
        for start in range(0, len(trace_ids), self._page_traces):
            chunk = trace_ids[start : start + self._page_traces]
            condition = {
                "type": "stringOptions",
                "column": "traceId",
                "operator": "any of",
                "value": chunk,
            }
            async for rows in self._rows([condition], SPAN_FIELDS, SPAN_PAGE):
                if rows:
                    yield [to_raw(r) for r in rows]

    async def session_spans(
        self, project: str, trace_filter: str | None, session_id: str
    ) -> list[RawSpan]:
        session = {"type": "string", "column": "sessionId", "operator": "=", "value": session_id}
        conditions = [*parse_filter(trace_filter), ROOT, session]
        trace_ids = [
            str(r["traceId"])
            async for rows in self._rows(conditions, "core", SPAN_PAGE)
            for r in rows
        ]
        if not trace_ids:
            return []
        return [s async for batch in self.trace_spans(project, trace_ids) for s in batch]
