from datetime import UTC, datetime, timedelta

import httpx
import pytest

from evalkit.contracts.enums import TraceImportStatus
from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.importer import ImportJob, guarded_import, run_import
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sources import TracePage

SINCE = datetime(2026, 10, 1, tzinfo=UTC)
UNTIL = datetime(2026, 10, 8, tzinfo=UTC)
MAPPING = MappingDocument.model_validate(
    {"schema_version": "1", "session_id": {"attribute": "conversation"}}
)


def spans_of(tid: str, start: datetime, session: str | None = None) -> list[RawSpan]:
    root = RawSpan(
        trace_id=tid,
        span_id=f"r{tid}",
        name="root",
        start_time=start,
        attributes={"conversation": session} if session else {},
    )
    child = RawSpan(
        trace_id=tid, span_id=f"c{tid}", parent_span_id=f"r{tid}", name="c", start_time=start
    )
    return [root, child]


class FakeClient:
    def __init__(self, fail: Exception | None = None):
        self.pages: list[TracePage] = []
        self.spans: dict[str, list[RawSpan]] = {}
        self.windows: list[tuple[datetime, datetime]] = []
        self.asked: list[list[str]] = []
        self._fail = fail

    def page(self, *traces: tuple[int, datetime, str | None]) -> "FakeClient":
        self.pages.append(TracePage(traces=[(f"t{n}", start) for n, start, _ in traces]))
        for n, start, session in traces:
            self.spans[f"t{n}"] = spans_of(f"t{n}", start, session)
        return self

    async def session_spans(self, project, session_id):
        return []

    async def trace_pages(self, project, since, until):
        self.windows.append((since, until))
        for p in self.pages:
            yield p
        if self._fail:
            raise self._fail

    async def trace_spans(self, project, trace_ids):
        self.asked.append(list(trace_ids))
        for tid in trace_ids:
            yield [s.model_copy() for s in self.spans[tid]]


def hour(n: int) -> datetime:
    return SINCE + timedelta(hours=n)


class FakeStore:
    def __init__(
        self,
        replies: list[TraceImportStatus | None] | None = None,
        resume_from: datetime = SINCE + timedelta(days=1),
        resume_trace_ids: list[str] | None = None,
    ):
        self.replies = list(replies or [])
        self.renew_replies: list[TraceImportStatus | None] = []
        self.renewed = 0
        self.resume_from = resume_from
        self.resume_trace_ids = resume_trace_ids or []
        self.advanced: list[dict] = []
        self.finished: list[tuple[TraceImportStatus, str | None]] = []

    async def job(self, import_id):
        return ImportJob(
            id=import_id,
            agent_id=7,
            trace_source_id=3,
            project="research-agent",
            until=UNTIL,
            resume_from=self.resume_from,
            resume_trace_ids=self.resume_trace_ids,
        )

    async def advance(self, import_id, holder, **progress):
        self.advanced.append(progress)
        return self.replies.pop(0) if self.replies else TraceImportStatus.RUNNING

    async def renew(self, import_id, holder):
        self.renewed += 1
        return self.renew_replies.pop(0) if self.renew_replies else TraceImportStatus.RUNNING

    async def finish(self, import_id, holder, status, error=None):
        self.finished.append((status, error))


class FakeSpans:
    def __init__(self):
        self.calls: list[tuple[int, list[RawSpan], int | None]] = []

    async def upsert(self, agent_id, spans, *, external_trace_source_id=None):
        self.calls.append((agent_id, spans, external_trace_source_id))
        return len(spans)


async def test_run_writes_the_spans_of_every_page_and_finishes_done():
    client = FakeClient().page((1, hour(1), "a"), (2, hour(2), None)).page((3, hour(3), "c"))
    store, spans = FakeStore(), FakeSpans()
    await run_import(12, "pod:a", client, store, spans, MAPPING)
    assert client.windows == [(SINCE + timedelta(days=1), UNTIL)]
    assert client.asked == [["t1", "t2"], ["t3"]]
    assert [(agent, len(batch), source) for agent, batch, source in spans.calls] == [(7, 2, 3)] * 3
    assert spans.calls[0][1][0].session_id_hint == "a"
    assert [a["resume_from"] for a in store.advanced] == [hour(2), hour(3)]
    assert [(a["traces"], a["spans"]) for a in store.advanced] == [(2, 4), (1, 2)]
    assert [a["sample_session"] for a in store.advanced] == ["a", "c"]
    assert store.finished == [(TraceImportStatus.DONE, None)]


async def test_stopping_finishes_stopped_after_the_current_page():
    client = FakeClient().page((1, hour(1), None)).page((2, hour(2), None))
    store, spans = FakeStore([TraceImportStatus.STOPPING]), FakeSpans()
    await run_import(12, "pod:a", client, store, spans, MAPPING)
    assert client.asked == [["t1"]]
    assert store.finished == [(TraceImportStatus.STOPPED, None)]


async def test_a_lost_lease_ends_without_finishing():
    client = FakeClient().page((1, hour(1), None)).page((2, hour(2), None))
    store, spans = FakeStore([None]), FakeSpans()
    await run_import(12, "pod:a", client, store, spans, MAPPING)
    assert len(spans.calls) == 1 and store.finished == []


async def test_guarded_import_records_the_error_and_reraises():
    boom = httpx.HTTPStatusError(
        "500", request=httpx.Request("GET", "u"), response=httpx.Response(500)
    )
    client = FakeClient(fail=boom).page((1, hour(1), None))
    store, spans = FakeStore(), FakeSpans()
    with pytest.raises(httpx.HTTPStatusError):
        await guarded_import(12, "pod:a", client, store, spans, MAPPING)
    assert len(store.advanced) == 1
    assert store.finished == [(TraceImportStatus.FAILED, "HTTPStatusError: 500")]


async def test_continue_skips_the_traces_already_written_at_resume_from():
    boundary = hour(4)
    client = (
        FakeClient()
        .page((1, boundary, None), (2, boundary, None))
        .page((3, boundary + timedelta(minutes=1), None))
    )
    store = FakeStore(resume_from=boundary, resume_trace_ids=["t1"])
    spans = FakeSpans()
    await run_import(12, "pod:a", client, store, spans, MAPPING)
    assert client.windows == [(boundary, UNTIL)]
    assert client.asked == [["t2"], ["t3"]]
    assert [(a["traces"], a["spans"]) for a in store.advanced] == [(1, 2), (1, 2)]
    assert [a["resume_trace_ids"] for a in store.advanced] == [["t1", "t2"], ["t3"]]


async def test_a_page_of_traces_already_written_asks_for_no_spans():
    client = FakeClient().page((1, hour(4), None))
    store = FakeStore(resume_from=hour(4), resume_trace_ids=["t1"])
    await run_import(12, "pod:a", client, store, FakeSpans(), MAPPING)
    assert client.asked == []
    assert [(a["traces"], a["spans"]) for a in store.advanced] == [(0, 0)]


async def test_pages_ending_on_one_start_time_keep_every_trace_at_it():
    client = FakeClient().page((1, hour(1), None)).page((2, hour(4), None)).page((3, hour(4), None))
    store = FakeStore()
    await run_import(12, "pod:a", client, store, FakeSpans(), MAPPING)
    assert [a["resume_trace_ids"] for a in store.advanced] == [["t1"], ["t2"], ["t2", "t3"]]
    assert sum(a["traces"] for a in store.advanced) == 3


async def test_the_lease_is_renewed_after_every_span_batch():
    client = FakeClient().page((1, hour(1), None), (2, hour(2), None)).page((3, hour(3), None))
    store = FakeStore()
    await run_import(12, "pod:a", client, store, FakeSpans(), MAPPING)
    assert store.renewed == 3 and len(store.advanced) == 2


async def test_a_lease_lost_mid_page_stops_before_the_next_batch():
    client = FakeClient().page((1, hour(1), None), (2, hour(2), None)).page((3, hour(3), None))
    store, spans = FakeStore(), FakeSpans()
    store.renew_replies = [None]
    await run_import(12, "pod:a", client, store, spans, MAPPING)
    assert len(spans.calls) == 1
    assert store.advanced == [] and store.finished == []
