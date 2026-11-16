import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel

from evalkit.contracts.enums import TraceImportStatus, TraceProvider
from evalkit.contracts.esf import MappingDocument
from evalkit.core.errors import error_text
from evalkit.core.traces.mapping import session_id_of
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sources import RateLimited, TraceSourceClient, newest_first

LOGGER = logging.getLogger(__name__)
IMPORT_LEASE_SECONDS = 180
RATE_LIMIT_GIVE_UP_SECONDS = 600
RATE_LIMIT_STEP_SECONDS = 30
IMPORT_STOPPED_ERROR = "the server stopped while importing"
ACTIVE_IMPORT = frozenset({TraceImportStatus.RUNNING, TraceImportStatus.STOPPING})


class ImportJob(BaseModel):
    id: int
    agent_id: int
    trace_source_id: int
    provider: TraceProvider
    project: str
    trace_filter: str | None
    since: datetime
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


class ImportRateLimited(Exception):
    pass


@dataclass
class _Progress:
    boundary: datetime
    written: set[str]
    waited: float = 0.0


async def _keep_lease(import_id: int, holder: str, store: ImportStore) -> bool:
    state = await store.renew(import_id, holder)
    if state is None:
        return False
    if state is TraceImportStatus.STOPPING:
        await store.finish(import_id, holder, TraceImportStatus.STOPPED)
        return False
    return True


async def _wait_out(
    import_id: int,
    holder: str,
    store: ImportStore,
    seconds: float,
    sleep: Callable[[float], Awaitable[None]],
) -> bool:
    left = seconds
    while left > 0:
        step = min(left, RATE_LIMIT_STEP_SECONDS)
        await sleep(step)
        left -= step
        if not await _keep_lease(import_id, holder, store):
            return False
    return True


async def _walk(
    import_id: int,
    holder: str,
    client: TraceSourceClient,
    store: ImportStore,
    spans: SpanWriter,
    mapping: MappingDocument | None,
    job: ImportJob,
    progress: _Progress,
) -> bool:
    if newest_first(job.provider):
        restarted = timedelta(milliseconds=1) if progress.written else timedelta(0)
        pages = client.trace_pages(
            job.project, job.trace_filter, job.since, progress.boundary + restarted
        )
    else:
        pages = client.trace_pages(job.project, job.trace_filter, progress.boundary, job.until)
    async for page in pages:
        if not page.traces:
            if not await _keep_lease(import_id, holder, store):
                return False
            continue
        boundary, written = progress.boundary, progress.written
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
                    return False
        at_last = {tid for tid, start in page.traces if start == page.last_start}
        progress.written = written | at_last if page.last_start == boundary else at_last
        progress.boundary = page.last_start
        if fresh:
            progress.waited = 0.0
        state = await store.advance(
            import_id,
            holder,
            resume_from=progress.boundary,
            resume_trace_ids=sorted(progress.written),
            traces=len(fresh),
            spans=span_count,
            sample_session=sample,
        )
        if state is None:
            return False
        if state is TraceImportStatus.STOPPING:
            await store.finish(import_id, holder, TraceImportStatus.STOPPED)
            return False
    return True


async def run_import(
    import_id: int,
    holder: str,
    client: TraceSourceClient,
    store: ImportStore,
    spans: SpanWriter,
    mapping: MappingDocument | None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    job = await store.job(import_id)
    progress = _Progress(job.resume_from, set(job.resume_trace_ids))
    while True:
        try:
            finished = await _walk(import_id, holder, client, store, spans, mapping, job, progress)
        except RateLimited as limited:
            if progress.waited + limited.retry_after > RATE_LIMIT_GIVE_UP_SECONDS:
                raise ImportRateLimited(
                    f"the trace source kept answering 429 Too Many Requests for "
                    f"{RATE_LIMIT_GIVE_UP_SECONDS:g} s; continue the import later"
                ) from limited
            progress.waited += limited.retry_after
            LOGGER.warning(
                "trace import %s rate limited; waiting %s s", import_id, limited.retry_after
            )
            if not await _wait_out(import_id, holder, store, limited.retry_after, sleep):
                return
            continue
        if finished:
            await store.finish(import_id, holder, TraceImportStatus.DONE)
        return


async def guarded_import(
    import_id: int,
    holder: str,
    client: TraceSourceClient,
    store: ImportStore,
    spans: SpanWriter,
    mapping: MappingDocument | None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    try:
        await run_import(import_id, holder, client, store, spans, mapping, sleep)
    except Exception as exc:
        await store.finish(import_id, holder, TraceImportStatus.FAILED, error_text(exc))
        raise
