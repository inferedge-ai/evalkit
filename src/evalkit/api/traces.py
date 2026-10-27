import logging
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ValidationError
from pydantic_core import to_json

from evalkit.api.access import ActorDep, ensure_active, require, workspace_ref
from evalkit.api.deps import (
    AgentStoreDep,
    MappingStoreDep,
    SessionDep,
    SessionReaderDep,
    SpanStoreDep,
)
from evalkit.api.experiments import slug_or_400
from evalkit.contracts.esf import MappingDocument
from evalkit.core.access import can_write
from evalkit.core.agents import AgentDefinition, UnknownAgent, explicit
from evalkit.core.traces.mapping import session_id_of
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sessions import SessionTrajectory
from evalkit.core.traces.sources import client_for
from evalkit.core.workspace import Reference
from evalkit.db.agents import AgentStore

router = APIRouter(prefix="/api", tags=["traces"])
LOGGER = logging.getLogger(__name__)


async def session_too_large(request: Request, exc: Exception) -> JSONResponse:
    LOGGER.warning("refused session read %s: %s", request.url.path, exc)
    return JSONResponse(status_code=413, content={"detail": str(exc)})


async def agent_or_404(agents: AgentStore, agent: str) -> tuple[Reference, AgentDefinition]:
    ref = slug_or_400(agent)
    return ref, await agents.get(explicit(ref))


async def require_write(session: SessionDep, actor: ActorDep, agent: AgentDefinition) -> None:
    ws = await workspace_ref(session, agent.workspace)
    require(can_write(actor, ws), f"you cannot change {agent.ref}'s mappings or traces")
    ensure_active(ws)


def require_otel(agent: AgentDefinition) -> None:
    if not agent.is_otel:
        raise HTTPException(
            status_code=400,
            detail=f"{agent.ref} takes ATIF trajectories; only OTel agents import traces",
        )


@router.get("/agents/{agent}/sessions/{session_id}/trajectory")
async def session_trajectory(
    agent: str,
    session_id: str,
    sessions: SessionReaderDep,
    mappings: MappingStoreDep,
    mapping: Literal["active", "draft"] = "active",
    esf: bool = False,
) -> Response:
    if mapping == "draft":
        record = await sessions.agent(agent)
        if await mappings.draft(record.id) is None:
            raise HTTPException(
                status_code=404,
                detail=f"no draft mapping for {agent}; push one with evalkit traces mapping push",
            )
    result = await sessions.read(agent, session_id, mapping=mapping, include_esf=esf)
    if result is None:
        raise UnknownAgent(agent)
    if result.esf is None:
        return Response(content=to_json(result), media_type="application/json")
    return StreamingResponse(_with_esf(result), media_type="application/json")


async def _with_esf(result: SessionTrajectory) -> AsyncIterator[bytes]:
    esf = result.esf or []
    yield to_json(result, exclude={"esf"})[:-1] + b',"esf":['
    for i, span in enumerate(esf):
        yield (b"," if i else b"") + to_json(span)
    yield b"]}"


async def _json_array(spans: AsyncIterator[RawSpan]) -> AsyncIterator[bytes]:
    yield b"["
    first = True
    async for span in spans:
        yield (b"" if first else b",") + to_json(span)
        first = False
    yield b"]"


@router.get("/agents/{agent}/sessions/{session_id}/spans")
async def session_spans(
    agent: str, session_id: str, sessions: SessionReaderDep
) -> StreamingResponse:
    record = await sessions.agent(agent)
    if record is None:
        raise UnknownAgent(agent)
    return StreamingResponse(
        _json_array(sessions.raw(record, session_id)), media_type="application/json"
    )


@router.get("/agents/{agent}/mappings")
async def list_mappings(agent: str, agents: AgentStoreDep, mappings: MappingStoreDep) -> list[dict]:
    _, record = await agent_or_404(agents, agent)
    return [m.model_dump(mode="json") for m in await mappings.list(record.id)]


@router.put("/agents/{agent}/mappings/draft")
async def put_draft(
    agent: str,
    body: dict,
    agents: AgentStoreDep,
    mappings: MappingStoreDep,
    session: SessionDep,
    actor: ActorDep,
    activate: bool = False,
) -> dict:
    _, record = await agent_or_404(agents, agent)
    await require_write(session, actor, record)
    try:
        document = MappingDocument.model_validate(body)
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(p) for p in first["loc"])
        raise HTTPException(status_code=400, detail=f"{location}: {first['msg']}") from exc
    draft = await mappings.put_draft(
        record.id, document.model_dump(by_alias=True, exclude_none=True), actor.username
    )
    if activate:
        draft = await mappings.activate(record.id, draft.version)
    return draft.model_dump(mode="json")


@router.post("/agents/{agent}/mappings/{version}/activate")
async def activate_mapping(
    agent: str,
    version: int,
    agents: AgentStoreDep,
    mappings: MappingStoreDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    _, record = await agent_or_404(agents, agent)
    await require_write(session, actor, record)
    try:
        return (await mappings.activate(record.id, version)).model_dump(mode="json")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


class ImportRequest(BaseModel):
    source: str
    project: str
    session_id: str


@router.post("/agents/{agent}/sessions/import")
async def import_session(
    agent: str,
    body: ImportRequest,
    request: Request,
    agents: AgentStoreDep,
    spans: SpanStoreDep,
    mappings: MappingStoreDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    _, record = await agent_or_404(agents, agent)
    await require_write(session, actor, record)
    require_otel(record)
    source = await request.app.state.trace_sources.resolve(body.source)
    client = client_for(
        source.provider,
        source.url,
        source.api_key.get_secret_value() if source.api_key else None,
        request.app.state.cfg.otel.import_page_traces,
    )
    try:
        fetched = await client.session_spans(body.project, body.session_id)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"{body.source}: {exc}") from exc
    active = await mappings.active(record.id)
    document = MappingDocument.model_validate(active.document) if active else None
    for span in fetched:
        span.session_id_hint = session_id_of(span.attributes, document) or body.session_id
    imported = await spans.upsert(record.id, fetched, external_trace_source_id=source.id)
    return {"imported": imported}
