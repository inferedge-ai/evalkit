from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, model_validator

from evalkit.api.access import StoresDep
from evalkit.api.auth import AdminUser
from evalkit.api.deps import EndpointRuleDep
from evalkit.contracts.enums import TraceProvider
from evalkit.core.endpoints import check_endpoint
from evalkit.core.traces.sources import check_api_key

router = APIRouter(prefix="/api/trace-sources", tags=["trace sources"])


@router.get("")
async def list_trace_sources(stores: StoresDep, include_archived: bool = False) -> list[dict]:
    rows = await stores.sources.list_sources(include_archived=include_archived)
    return [row.model_dump(mode="json") for row in rows]


class CreateTraceSource(BaseModel):
    name: str
    provider: TraceProvider
    url: str
    api_key: str | None = None

    @model_validator(mode="after")
    def key_fits_provider(self) -> "CreateTraceSource":
        check_api_key(self.provider, self.api_key)
        return self


@router.post("")
async def create_trace_source(
    body: CreateTraceSource,
    stores: StoresDep,
    rule: EndpointRuleDep,
    admin: AdminUser,
) -> dict:
    await check_endpoint(body.url, rule=rule)
    created = await stores.sources.create(body.name, body.provider, body.url, body.api_key)
    return created.model_dump(mode="json")


class EditTraceSource(BaseModel):
    url: str | None = None
    api_key: str | None = None


@router.patch("/{name}")
async def edit_trace_source(
    name: str,
    body: EditTraceSource,
    stores: StoresDep,
    rule: EndpointRuleDep,
    admin: AdminUser,
) -> dict:
    if body.url is not None:
        await check_endpoint(body.url, rule=rule)
    if body.api_key is not None:
        current = await stores.sources.resolve(name)
        try:
            check_api_key(current.provider, body.api_key)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    edited = await stores.sources.edit(name, url=body.url, api_key=body.api_key)
    return edited.model_dump(mode="json")


@router.post("/{name}/archive")
async def archive_trace_source(name: str, stores: StoresDep, admin: AdminUser) -> dict:
    return (await stores.sources.archive(name)).model_dump(mode="json")
