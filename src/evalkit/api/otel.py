from __future__ import annotations

import gzip
import io
import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from evalkit.api.access import load_actor, workspace_ref
from evalkit.api.auth import CollectorPrincipal, Principal
from evalkit.api.deps import AgentStoreDep, ConfigDep, MappingStoreDep, SessionDep, SpanStoreDep
from evalkit.contracts.esf import MappingDocument
from evalkit.core.access import Actor, WorkspaceRef, can_write
from evalkit.core.agents import AgentDefinition, UnknownAgent, explicit
from evalkit.core.traces.mapping import session_id_of
from evalkit.core.traces.raw import RawSpan
from evalkit.core.workspace import Reference, parse_reference, slug
from evalkit.db.agents import AgentStore
from evalkit.db.models import User

router = APIRouter(prefix="/api/otel/v1", tags=["otel"])
LOGGER = logging.getLogger(__name__)

AGENT_HEADER = "X-Evalkit-Agent"
AGENT_ATTRIBUTE = "evalkit.agent"
PROTOBUF = "application/x-protobuf"
SPAN_KINDS = {
    0: "UNSPECIFIED",
    1: "INTERNAL",
    2: "SERVER",
    3: "CLIENT",
    4: "PRODUCER",
    5: "CONSUMER",
}
STATUS_CODES = {0: "UNSET", 1: "OK", 2: "ERROR"}


def _any_value(value: AnyValue) -> Any:
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_any_value(v) for v in value.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _any_value(kv.value) for kv in value.kvlist_value.values}
    if kind == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, kind)


def _attributes(items: Any) -> dict[str, Any]:
    return {kv.key: _any_value(kv.value) for kv in items}


def _time(nanos: int) -> datetime | None:
    return datetime.fromtimestamp(nanos / 1e9, tz=UTC) if nanos else None


def _iso(nanos: int) -> str | None:
    at = _time(nanos)
    return at.isoformat() if at else None


def _from_proto(span: ProtoSpan, resource: dict[str, Any]) -> RawSpan:
    start = _time(span.start_time_unix_nano)
    if start is None:
        raise HTTPException(status_code=400, detail=f"span {span.name!r} has no start time")
    return RawSpan(
        trace_id=span.trace_id.hex(),
        span_id=span.span_id.hex(),
        parent_span_id=span.parent_span_id.hex() or None,
        name=span.name,
        kind=SPAN_KINDS.get(span.kind, "INTERNAL"),
        start_time=start,
        end_time=_time(span.end_time_unix_nano),
        status_code=STATUS_CODES.get(span.status.code, "UNSET"),
        status_message=span.status.message or None,
        attributes=_attributes(span.attributes),
        events=[
            {
                "name": e.name,
                "time": _iso(e.time_unix_nano),
                "attributes": _attributes(e.attributes),
            }
            for e in span.events
        ],
        resource=resource,
    )


def _json_any(value: dict[str, Any]) -> Any:
    if "stringValue" in value:
        return value["stringValue"]
    if "intValue" in value:
        return int(value["intValue"])
    if "doubleValue" in value:
        return float(value["doubleValue"])
    if "boolValue" in value:
        return bool(value["boolValue"])
    if "arrayValue" in value:
        return [_json_any(v) for v in value["arrayValue"].get("values", [])]
    if "kvlistValue" in value:
        return {kv["key"]: _json_any(kv["value"]) for kv in value["kvlistValue"].get("values", [])}
    if "bytesValue" in value:
        return value["bytesValue"]
    return None


def _json_attributes(items: list[dict[str, Any]] | None) -> dict[str, Any]:
    return {kv["key"]: _json_any(kv.get("value", {})) for kv in items or []}


def _from_json(span: dict[str, Any], resource: dict[str, Any]) -> RawSpan:
    start = _time(int(span.get("startTimeUnixNano") or 0))
    if start is None:
        raise HTTPException(status_code=400, detail="span without startTimeUnixNano")
    status = span.get("status") or {}
    return RawSpan(
        trace_id=span["traceId"],
        span_id=span["spanId"],
        parent_span_id=span.get("parentSpanId") or None,
        name=span.get("name", ""),
        kind=SPAN_KINDS.get(int(span.get("kind") or 0), "INTERNAL"),
        start_time=start,
        end_time=_time(int(span.get("endTimeUnixNano") or 0)),
        status_code=STATUS_CODES.get(int(status.get("code") or 0), "UNSET"),
        status_message=status.get("message") or None,
        attributes=_json_attributes(span.get("attributes")),
        events=[
            {
                "name": e.get("name"),
                "time": _iso(int(e.get("timeUnixNano") or 0)),
                "attributes": _json_attributes(e.get("attributes")),
            }
            for e in span.get("events") or []
        ],
        resource=resource,
    )


def _decode(body: bytes, content_type: str) -> list[RawSpan]:
    spans: list[RawSpan] = []
    if content_type.startswith(PROTOBUF):
        request = ExportTraceServiceRequest.FromString(body)
        for resource_spans in request.resource_spans:
            resource = _attributes(resource_spans.resource.attributes)
            for scope_spans in resource_spans.scope_spans:
                spans.extend(_from_proto(span, resource) for span in scope_spans.spans)
        return spans
    if content_type.startswith("application/json"):
        payload = json.loads(body)
        for resource_spans in payload.get("resourceSpans", []):
            resource = _json_attributes((resource_spans.get("resource") or {}).get("attributes"))
            for scope_spans in resource_spans.get("scopeSpans", []):
                spans.extend(_from_json(span, resource) for span in scope_spans.get("spans", []))
        return spans
    raise HTTPException(
        status_code=415,
        detail=f"unsupported content type {content_type!r}; send {PROTOBUF} or application/json",
    )


def decode_otlp(body: bytes, content_type: str) -> list[RawSpan]:
    try:
        return _decode(body, content_type)
    except (DecodeError, ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"malformed OTLP body: {exc!r}") from exc


class Rejected(HTTPException):
    def __init__(self, status_code: int, detail: str, reason: str, agent: str | None) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.reason = reason
        self.agent = agent


def _reason(exc: StarletteHTTPException) -> str:
    if isinstance(exc, Rejected):
        return exc.reason
    if exc.status_code == 401:
        return "expired token" if exc.detail == "token expired" else "invalid token"
    if exc.status_code == 403:
        return "forbidden"
    if exc.status_code == 413:
        return "too large"
    return "malformed"


async def log_rejection(request: Request, exc: Exception) -> Response:
    assert isinstance(exc, StarletteHTTPException)
    if request.url.path.startswith(router.prefix):
        agent = exc.agent if isinstance(exc, Rejected) else request.headers.get(AGENT_HEADER)
        reason = _reason(exc)
        LOGGER.warning(
            "otel export rejected: status=%s reason=%s agent=%s detail=%s",
            exc.status_code,
            reason,
            agent,
            exc.detail,
            extra={
                "otel_rejection": {
                    "status": exc.status_code,
                    "reason": reason,
                    "agent": agent,
                    "detail": exc.detail,
                }
            },
        )
    return await http_exception_handler(request, exc)


def _named_agent(span: RawSpan, header: str | None) -> str | None:
    for source in (span.attributes, span.resource):
        value = source.get(AGENT_ATTRIBUTE)
        if value:
            return str(value)
    return header


def _token_agent(named: str | None, bound: AgentDefinition) -> None:
    ref = Reference(bound.name, bound.workspace)
    if named is None:
        return
    try:
        parsed = parse_reference(named, bound.workspace)
    except ValueError as exc:
        raise Rejected(400, str(exc), "malformed", named) from exc
    if parsed != ref:
        raise Rejected(
            403, f"token is bound to {slug(ref)}, batch names {named}", "forbidden", named
        )


def _user_agent(named: str | None, user: User) -> Reference:
    if named is None:
        raise Rejected(
            400,
            f"name the agent with the {AGENT_HEADER} header or the {AGENT_ATTRIBUTE} span or "
            "resource attribute",
            "malformed",
            None,
        )
    try:
        return parse_reference(named, user.username)
    except ValueError as exc:
        raise Rejected(400, str(exc), "malformed", named) from exc


async def _user_record(
    ref: Reference, actor: Actor, session: AsyncSession, agents: AgentStore
) -> AgentDefinition:
    agent = await agents.find(explicit(ref))
    if agent is None:
        unknown = UnknownAgent(explicit(ref)).args[0]
        raise Rejected(404, unknown, "unknown agent", explicit(ref))
    ws = await workspace_ref(session, agent.workspace)
    if not can_write(actor, ws):
        raise Rejected(403, f"you cannot export traces for {agent.ref}", "forbidden", agent.ref)
    return _live(agent, ws)


def _live(agent: AgentDefinition, ws: WorkspaceRef) -> AgentDefinition:
    if ws.archived:
        raise Rejected(409, f"workspace {ws.name} is archived", "archived", agent.ref)
    if agent.archived:
        raise Rejected(409, f"agent {agent.ref} is archived", "archived", agent.ref)
    return agent


async def _resolve(
    principal: Principal,
    groups: dict[str | None, list[RawSpan]],
    session: AsyncSession,
    agents: AgentStore,
) -> list[tuple[AgentDefinition, list[RawSpan]]]:
    if principal.agent_id is not None:
        bound = await agents.get_by_id(principal.agent_id)
        for named in groups:
            _token_agent(named, bound)
        _live(bound, await workspace_ref(session, bound.workspace))
        return [(bound, [span for spans in groups.values() for span in spans])]
    assert principal.user is not None
    user = principal.user
    actor = await load_actor(session, user)
    refs = {named: _user_agent(named, user) for named in groups}
    by_ref: dict[Reference, list[RawSpan]] = {}
    for named, spans in groups.items():
        by_ref.setdefault(refs[named], []).extend(spans)
    return [
        (await _user_record(ref, actor, session, agents), spans) for ref, spans in by_ref.items()
    ]


@router.post("/traces")
async def export_traces(
    request: Request,
    principal: CollectorPrincipal,
    cfg: ConfigDep,
    agents: AgentStoreDep,
    session: SessionDep,
    spans_store: SpanStoreDep,
    mappings: MappingStoreDep,
) -> Response:
    header = request.headers.get(AGENT_HEADER)
    body = await request.body()
    if request.headers.get("content-encoding") == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as unzipped:
            body = unzipped.read(cfg.otel.max_batch_bytes + 1)
        if len(body) > cfg.otel.max_batch_bytes:
            raise Rejected(
                413,
                f"decompressed batch exceeds otel.max_batch_bytes ({cfg.otel.max_batch_bytes})",
                "too large",
                header,
            )
    content_type = request.headers.get("content-type", "")
    spans = decode_otlp(body, content_type)
    groups: dict[str | None, list[RawSpan]] = {}
    for span in spans:
        groups.setdefault(_named_agent(span, header), []).append(span)
    for record, agent_spans in await _resolve(principal, groups, session, agents):
        active = await mappings.active(record.id)
        document = MappingDocument.model_validate(active.document) if active else None
        if document is None:
            LOGGER.warning(
                "agent %s has no active span mapping; its spans are stored without a session id",
                record.ref,
            )
        for span in agent_spans:
            span.session_id_hint = session_id_of(span.attributes, document)
        await spans_store.upsert(record.id, agent_spans)
    if content_type.startswith(PROTOBUF):
        return Response(
            content=ExportTraceServiceResponse().SerializeToString(), media_type=PROTOBUF
        )
    return Response(content=b"{}", media_type="application/json")
