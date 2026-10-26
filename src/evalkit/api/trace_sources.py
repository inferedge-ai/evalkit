from fastapi import APIRouter, Request
from pydantic import BaseModel

from evalkit.api.auth import AdminUser
from evalkit.contracts.enums import TraceProvider
from evalkit.db.trace_sources import TraceSourceStore

router = APIRouter(prefix="/api/trace-sources", tags=["trace sources"])


def _store(request: Request) -> TraceSourceStore:
    return request.app.state.trace_sources


@router.get("")
async def list_trace_sources(request: Request, include_archived: bool = False) -> list[dict]:
    rows = await _store(request).list_sources(include_archived=include_archived)
    return [row.model_dump(mode="json") for row in rows]


class CreateTraceSource(BaseModel):
    name: str
    provider: TraceProvider
    url: str
    api_key: str | None = None


@router.post("")
async def create_trace_source(body: CreateTraceSource, request: Request, admin: AdminUser) -> dict:
    created = await _store(request).create(body.name, body.provider, body.url, body.api_key)
    return created.model_dump(mode="json")


class EditTraceSource(BaseModel):
    url: str | None = None
    api_key: str | None = None


@router.patch("/{name}")
async def edit_trace_source(
    name: str, body: EditTraceSource, request: Request, admin: AdminUser
) -> dict:
    edited = await _store(request).edit(name, url=body.url, api_key=body.api_key)
    return edited.model_dump(mode="json")


@router.post("/{name}/archive")
async def archive_trace_source(name: str, request: Request, admin: AdminUser) -> dict:
    return (await _store(request).archive(name)).model_dump(mode="json")
