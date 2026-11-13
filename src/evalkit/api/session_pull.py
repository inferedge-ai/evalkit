import httpx2

from evalkit.contracts.esf import MappingDocument
from evalkit.core.agents import explicit
from evalkit.core.traces.session_import import SessionPullFailed, pull_session
from evalkit.core.traces.sources import TraceSourceClient, client_for
from evalkit.core.workspace import parse_slug
from evalkit.db.agents import AgentStore
from evalkit.db.mappings import MappingStore
from evalkit.db.spans import SpanStore
from evalkit.db.trace_sources import ResolvedTraceSource, TraceSourceStore, UnknownTraceSource


def source_client(source: ResolvedTraceSource, page_traces: int) -> TraceSourceClient:
    return client_for(
        source.provider,
        source.url,
        source.api_key.get_secret_value() if source.api_key else None,
        page_traces,
    )


async def active_mapping(mappings: MappingStore, agent_id: int) -> MappingDocument | None:
    active = await mappings.active(agent_id)
    return MappingDocument.model_validate(active.document) if active else None


class ServerSessionPuller:
    def __init__(
        self,
        agents: AgentStore,
        sources: TraceSourceStore,
        mappings: MappingStore,
        spans: SpanStore,
        page_traces: int,
    ):
        self._agents = agents
        self._sources = sources
        self._mappings = mappings
        self._spans = spans
        self._page_traces = page_traces

    async def __call__(self, agent: str, session_id: str) -> None:
        record = await self._agents.get(explicit(parse_slug(agent)))
        settings = record.settings
        if settings.trace_source is None or settings.project is None:
            return
        try:
            source = await self._sources.resolve(settings.trace_source)
            await pull_session(
                source_client(source, self._page_traces),
                self._spans,
                await active_mapping(self._mappings, record.id),
                agent_id=record.id,
                source_id=source.id,
                project=settings.project,
                session_id=session_id,
            )
        except (httpx2.HTTPError, UnknownTraceSource) as exc:
            raise SessionPullFailed(f"trace source {settings.trace_source}: {exc}") from exc
