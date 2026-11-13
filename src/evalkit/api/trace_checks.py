import httpx2
from fastapi import HTTPException

from evalkit.api.access import TenantStores
from evalkit.api.session_pull import build_source_client
from evalkit.contracts.settings import AgentSettings
from evalkit.core.endpoints import EndpointRule, check_endpoint
from evalkit.core.traces.sources import UnknownProject
from evalkit.db.trace_sources import ResolvedTraceSource


async def resolve_import_source(
    stores: TenantStores, name: str, *, rule: EndpointRule
) -> ResolvedTraceSource:
    source = await stores.sources.resolve(name)
    await check_endpoint(source.url, rule=rule)
    return source


async def check_trace_source(
    stores: TenantStores,
    settings: AgentSettings,
    *,
    otel: bool,
    rule: EndpointRule,
    page_traces: int,
) -> None:
    if settings.trace_source is None or settings.project is None:
        return
    if not otel:
        raise HTTPException(
            status_code=400,
            detail="only an OTel agent (trajectory_source otel) takes a trace source",
        )
    source = await resolve_import_source(stores, settings.trace_source, rule=rule)
    try:
        await build_source_client(source, page_traces).check_project(settings.project)
    except UnknownProject as exc:
        raise HTTPException(
            status_code=400,
            detail=f"project {settings.project} not found in trace source {source.name} "
            f"(provider {source.provider.value}); check the name in its UI",
        ) from exc
    except httpx2.HTTPStatusError as exc:
        if exc.response.status_code not in (401, 403):
            raise HTTPException(
                status_code=502,
                detail=f"trace source {source.name} at {source.url} did not answer: {exc}",
            ) from exc
        raise HTTPException(
            status_code=400,
            detail=f"trace source {source.name} refused its API key "
            f"({exc.response.status_code}); an admin sets a new one with "
            f"evalkit trace-sources edit {source.name} --api-key-env <VAR>",
        ) from exc
    except httpx2.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"trace source {source.name} at {source.url} did not answer: {exc}",
        ) from exc
