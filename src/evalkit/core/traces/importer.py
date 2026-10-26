from datetime import datetime
from typing import Protocol

from pydantic import BaseModel

from evalkit.contracts.enums import TraceImportStatus
from evalkit.contracts.esf import MappingDocument
from evalkit.core.runner import error_text
from evalkit.core.traces.mapping import session_id_of
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sources import TraceSourceClient

IMPORT_LEASE_SECONDS = 180
IMPORT_STOPPED_ERROR = "the server stopped while importing"
ACTIVE_IMPORT = frozenset({TraceImportStatus.RUNNING, TraceImportStatus.STOPPING})


class ImportJob(BaseModel):
    id: int
    agent_id: int
    trace_source_id: int
    project: str
    until: datetime
    resume_from: datetime
    resume_trace_ids: list[str]


class ImportStore(Protocol):
    async def job(self, import_id: int) -> ImportJob: ...

    async def advance(
        self,
        import_id: int,
        holder: str,
        *,
        resume_from: datetime,
        resume_trace_ids: list[str],
        traces: int,
        spans: int,
        sample_session: str | None,
    ) -> TraceImportStatus | None: ...

    async def renew(self, import_id: int, holder: str) -> TraceImportStatus | None: ...

    async def finish(
        self, import_id: int, holder: str, status: TraceImportStatus, error: str | None = None
    ) -> None: ...


class SpanWriter(Protocol):
    async def upsert(
        self, agent_id: int, spans: list[RawSpan], *, external_trace_source_id: int | None = None
    ) -> int: ...


async def run_import(
    import_id: int,
    holder: str,
    client: TraceSourceClient,
    store: ImportStore,
    spans: SpanWriter,
    mapping: MappingDocument | None,
) -> None:
    job = await store.job(import_id)
    boundary, written = job.resume_from, set(job.resume_trace_ids)
    async for page in client.trace_pages(job.project, job.resume_from, job.until):
        fresh = [tid for tid, start in page.traces if start != boundary or tid not in written]
        span_count, sample = 0, None
        if fresh:
            async for batch in client.trace_spans(job.project, fresh):
                for span in batch:
                    span.session_id_hint = session_id_of(span.attributes, mapping)
                await spans.upsert(
                    job.agent_id, batch, external_trace_source_id=job.trace_source_id
                )
                span_count += len(batch)
                roots = [s.session_id_hint for s in batch if s.parent_span_id is None]
                sample = next((r for r in reversed(roots) if r), sample)
                if await store.renew(import_id, holder) is None:
                    return
        at_last = {tid for tid, start in page.traces if start == page.last_start}
        written = written | at_last if page.last_start == boundary else at_last
        boundary = page.last_start
        state = await store.advance(
            import_id,
            holder,
            resume_from=boundary,
            resume_trace_ids=sorted(written),
            traces=len(fresh),
            spans=span_count,
            sample_session=sample,
        )
        if state is None:
            return
        if state is TraceImportStatus.STOPPING:
            await store.finish(import_id, holder, TraceImportStatus.STOPPED)
            return
    await store.finish(import_id, holder, TraceImportStatus.DONE)


async def guarded_import(
    import_id: int,
    holder: str,
    client: TraceSourceClient,
    store: ImportStore,
    spans: SpanWriter,
    mapping: MappingDocument | None,
) -> None:
    try:
        await run_import(import_id, holder, client, store, spans, mapping)
    except Exception as exc:
        await store.finish(import_id, holder, TraceImportStatus.FAILED, error_text(exc))
        raise
