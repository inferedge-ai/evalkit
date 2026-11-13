from collections.abc import Awaitable, Callable

from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.importer import SpanWriter
from evalkit.core.traces.mapping import session_id_of
from evalkit.core.traces.sessions import SessionReader, SessionTrajectory
from evalkit.core.traces.sources import TraceSourceClient


class SessionPullFailed(Exception):
    pass


async def pull_session(
    client: TraceSourceClient,
    spans: SpanWriter,
    mapping: MappingDocument | None,
    *,
    agent_id: int,
    source_id: int,
    project: str,
    session_id: str,
) -> int:
    fetched = await client.session_spans(project, session_id)
    for span in fetched:
        span.session_id_hint = session_id_of(span.attributes, mapping) or session_id
    return await spans.upsert(agent_id, fetched, external_trace_source_id=source_id)


class TraceSourceSessionReader:
    def __init__(self, reader: SessionReader, pull: Callable[[str, str], Awaitable[None]]):
        self._reader = reader
        self._pull = pull

    async def trajectory(self, agent: str, session_id: str) -> SessionTrajectory:
        await self._pull(agent, session_id)
        return await self._reader.trajectory(agent, session_id)
