import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import AwareDatetime, BaseModel, model_validator

from evalkit.api.access import ActorDep
from evalkit.api.deps import AgentStoreDep, HolderDep, MappingStoreDep, SessionDep
from evalkit.api.experiments import task_holder
from evalkit.api.traces import agent_or_404, require_otel, require_write
from evalkit.contracts.enums import TraceImportStatus
from evalkit.contracts.esf import MappingDocument
from evalkit.core.agents import AgentDefinition
from evalkit.core.runner import error_text
from evalkit.core.traces.importer import IMPORT_STOPPED_ERROR, guarded_import
from evalkit.core.traces.sources import client_for
from evalkit.db.mappings import MappingStore
from evalkit.db.trace_imports import TraceImportStore, TraceImportView
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


def _imports(request: Request) -> TraceImportStore:
    return request.app.state.trace_imports


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
    store = _imports(request)
    task = asyncio.create_task(
        guarded_import(import_id, holder, client, store, request.app.state.spans, mapping),
        name=f"import:{import_id}",
    )
    registry: ImportTasks = request.app.state.import_tasks
    registry[import_id] = (holder, task)
    task.add_done_callback(lambda t: registry.pop(import_id, None))
    task.add_done_callback(_log_failure)


async def interrupt_imports(store: TraceImportStore, registry: ImportTasks) -> None:
    tracked = list(registry.items())
    for _, (_, task) in tracked:
        task.cancel()
    await asyncio.gather(*(task for _, (_, task) in tracked), return_exceptions=True)
    for import_id, (holder, task) in tracked:
        if task.cancelled():
            await store.finish(import_id, holder, TraceImportStatus.FAILED, IMPORT_STOPPED_ERROR)


@router.post("/{agent}/imports", status_code=202)
async def start_import(
    agent: str,
    body: RangeImportRequest,
    request: Request,
    agents: AgentStoreDep,
    mappings: MappingStoreDep,
    session: SessionDep,
    actor: ActorDep,
    pod: HolderDep,
) -> TraceImportView:
    _, record = await agent_or_404(agents, agent)
    await require_write(session, actor, record)
    require_otel(record)
    mapping = await _session_mapping(mappings, record)
    source = await request.app.state.trace_sources.resolve(body.source)
    holder = task_holder(pod)
    view = await _imports(request).create(
        record.id, source.id, body.project, body.since, body.until, actor.id, holder
    )
    _launch(request, view.id, holder, source, mapping)
    return view


@router.get("/{agent}/imports")
async def list_imports(
    agent: str, request: Request, agents: AgentStoreDep
) -> list[TraceImportView]:
    _, record = await agent_or_404(agents, agent)
    return await _imports(request).recent(record.id)


@router.get("/{agent}/imports/{import_id}")
async def get_import(
    agent: str, import_id: int, request: Request, agents: AgentStoreDep
) -> TraceImportView:
    _, record = await agent_or_404(agents, agent)
    return await _imports(request).get(record.id, import_id)


@router.post("/{agent}/imports/{import_id}/stop")
async def stop_import(
    agent: str,
    import_id: int,
    request: Request,
    agents: AgentStoreDep,
    session: SessionDep,
    actor: ActorDep,
) -> TraceImportView:
    _, record = await agent_or_404(agents, agent)
    await require_write(session, actor, record)
    return await _imports(request).stop(record.id, import_id)


@router.post("/{agent}/imports/{import_id}/continue")
async def continue_import(
    agent: str,
    import_id: int,
    request: Request,
    agents: AgentStoreDep,
    mappings: MappingStoreDep,
    session: SessionDep,
    actor: ActorDep,
    pod: HolderDep,
) -> TraceImportView:
    _, record = await agent_or_404(agents, agent)
    await require_write(session, actor, record)
    require_otel(record)
    mapping = await _session_mapping(mappings, record)
    store = _imports(request)
    current = await store.get(record.id, import_id)
    source = await request.app.state.trace_sources.resolve(current.source)
    holder = task_holder(pod)
    view = await store.restart(record.id, import_id, holder)
    _launch(request, view.id, holder, source, mapping)
    return view
