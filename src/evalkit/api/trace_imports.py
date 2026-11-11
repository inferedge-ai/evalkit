import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, model_validator

from evalkit.api.access import ActorDep, StoresDep, TenantStores
from evalkit.api.deps import EndpointRuleDep, HolderDep
from evalkit.api.experiments import task_holder
from evalkit.api.traces import agent_or_404, require_otel, require_write, resolve_import_source
from evalkit.contracts.enums import TraceImportStatus
from evalkit.contracts.esf import MappingDocument
from evalkit.core.agents import AgentDefinition
from evalkit.core.errors import error_text
from evalkit.core.traces.importer import IMPORT_STOPPED_ERROR, guarded_import
from evalkit.core.traces.sources import client_for
from evalkit.db.mappings import MappingStore
from evalkit.db.trace_imports import TraceImportStore, TraceImportView, tenant_of_import
from evalkit.db.trace_sources import ResolvedTraceSource

router = APIRouter(prefix="/api/agents", tags=["traces"])
LOGGER = logging.getLogger(__name__)

ImportTasks = dict[int, tuple[str, asyncio.Task]]
NO_SESSION_MAPPING = (
    "activate a mapping with session_id before importing a time range; "
    "import single sessions to try mappings first"
)


class RangeImportRequest(BaseModel):
    source: str
    project: str
    since: AwareDatetime
    until: AwareDatetime

    @model_validator(mode="after")
    def ordered(self) -> "RangeImportRequest":
        if self.since >= self.until:
            raise ValueError("since must be before until")
        return self


async def _session_mapping(mappings: MappingStore, agent: AgentDefinition) -> MappingDocument:
    active = await mappings.active(agent.id)
    document = MappingDocument.model_validate(active.document) if active else None
    if document is None or document.session_id is None:
        raise HTTPException(status_code=409, detail=NO_SESSION_MAPPING)
    return document


def _log_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        LOGGER.error("trace import task %s failed: %s", task.get_name(), error_text(exc))


def _launch(
    request: Request,
    stores: TenantStores,
    import_id: int,
    holder: str,
    source: ResolvedTraceSource,
    mapping: MappingDocument,
) -> None:
    client = client_for(
        source.provider,
        source.url,
        source.api_key.get_secret_value() if source.api_key else None,
        request.app.state.cfg.otel.import_page_traces,
    )
    task = asyncio.create_task(
        guarded_import(import_id, holder, client, stores.imports, stores.spans, mapping),
        name=f"import:{import_id}",
    )
    registry: ImportTasks = request.app.state.import_tasks
    registry[import_id] = (holder, task)
    task.add_done_callback(lambda t: registry.pop(import_id, None))
    task.add_done_callback(_log_failure)


async def interrupt_imports(session_factory, registry: ImportTasks) -> None:
    tracked = list(registry.items())
    for _, (_, task) in tracked:
        task.cancel()
    await asyncio.gather(*(task for _, (_, task) in tracked), return_exceptions=True)
    for import_id, (holder, task) in tracked:
        if task.cancelled():
            store = TraceImportStore(
                session_factory, await tenant_of_import(session_factory, import_id)
            )
            await store.finish(import_id, holder, TraceImportStatus.FAILED, IMPORT_STOPPED_ERROR)


@router.post("/{agent}/imports", status_code=202)
async def start_import(
    agent: str,
    body: RangeImportRequest,
    request: Request,
    stores: StoresDep,
    actor: ActorDep,
    pod: HolderDep,
    rule: EndpointRuleDep,
) -> TraceImportView:
    _, record = await agent_or_404(stores.agents, agent)
    await require_write(stores, actor, record)
    require_otel(record)
    mapping = await _session_mapping(stores.mappings, record)
    source = await resolve_import_source(stores, body.source, rule=rule)
    holder = task_holder(pod)
    view = await stores.imports.create(
        record.id, source.id, body.project, body.since, body.until, actor.id, holder
    )
    _launch(request, stores, view.id, holder, source, mapping)
    return view


@router.get("/{agent}/imports")
async def list_imports(agent: str, stores: StoresDep) -> list[TraceImportView]:
    _, record = await agent_or_404(stores.agents, agent)
    return await stores.imports.recent(record.id)


@router.get("/{agent}/imports/{import_id}")
async def get_import(agent: str, import_id: int, stores: StoresDep) -> TraceImportView:
    _, record = await agent_or_404(stores.agents, agent)
    return await stores.imports.get(record.id, import_id)


@router.post("/{agent}/imports/{import_id}/stop")
async def stop_import(
    agent: str,
    import_id: int,
    stores: StoresDep,
    actor: ActorDep,
) -> TraceImportView:
    _, record = await agent_or_404(stores.agents, agent)
    await require_write(stores, actor, record)
    return await stores.imports.stop(record.id, import_id)


@router.post("/{agent}/imports/{import_id}/continue")
async def continue_import(
    agent: str,
    import_id: int,
    request: Request,
    stores: StoresDep,
    actor: ActorDep,
    pod: HolderDep,
    rule: EndpointRuleDep,
) -> TraceImportView:
    _, record = await agent_or_404(stores.agents, agent)
    await require_write(stores, actor, record)
    require_otel(record)
    mapping = await _session_mapping(stores.mappings, record)
    current = await stores.imports.get(record.id, import_id)
    source = await resolve_import_source(stores, current.source, rule=rule)
    holder = task_holder(pod)
    view = await stores.imports.restart(record.id, import_id, holder)
    _launch(request, stores, view.id, holder, source, mapping)
    return view
