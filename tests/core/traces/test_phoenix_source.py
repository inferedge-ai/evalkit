from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from respx import MockResponse

from evalkit.contracts.enums import TraceProvider
from evalkit.core.traces.sources import BadTraceFilter, RateLimited, UnknownProject, client_for
from evalkit.core.traces.sources.phoenix import PhoenixClient, _to_raw, flatten_attributes

PHOENIX = "https://phoenix.example.com"
TRACES = f"{PHOENIX}/v1/projects/research-agent/traces"
VERSION = f"{PHOENIX}/arize_phoenix_version"
SPANS = f"{PHOENIX}/v1/projects/research-agent/spans"


def phoenix_trace(n: int, start: str = "2026-10-01T00:00:00+00:00") -> dict:
    return {
        "id": f"VHJhY2U6{n}",
        "trace_id": f"{n:032x}",
        "project_id": "UHJvamVjdDox",
        "start_time": start,
        "end_time": start,
        "spans": [],
    }


def phoenix_span(
    n: int, session: str | None = "sess-9", start: str = "2026-10-01T00:00:00+00:00"
) -> dict:
    span = {
        "id": f"U3BhbjE{n}",
        "name": "ChatCompletion",
        "context": {"trace_id": f"{n:032x}", "span_id": f"{n:016x}"},
        "span_kind": "LLM",
        "parent_id": None,
        "start_time": start,
        "end_time": "2026-10-01T00:00:01+00:00",
        "status_code": "OK",
        "status_message": "",
        "attributes": {
            "openinference.span.kind": "LLM",
            "session.id": session,
            "llm.model_name": "m",
            "llm.input_messages": [{"message.role": "user", "message.content": "q"}],
            "llm.token_count.prompt": 3,
            "tags": ["a", "b"],
        },
        "events": [
            {
                "name": "exception",
                "timestamp": "2026-10-01T00:00:00.5+00:00",
                "attributes": {"exception.message": "x"},
            }
        ],
    }
    if session is None:
        del span["attributes"]["session.id"]
    return span


def client() -> PhoenixClient:
    return PhoenixClient(PHOENIX, "pk", 25)


def test_flatten_attributes():
    nested = {"llm": {"input_messages": [{"message": {"role": "user"}}], "x": [1, 2]}, "k": "v"}
    assert flatten_attributes(nested) == {
        "llm.input_messages.0.message.role": "user",
        "llm.x": [1, 2],
        "k": "v",
    }


async def test_session_spans_lists_the_traces_then_reads_their_spans_on_every_page(httpx2_mock):
    traces = httpx2_mock.get(TRACES).mock(
        side_effect=[
            MockResponse(200, json={"data": [phoenix_trace(1)], "next_cursor": "c2"}),
            MockResponse(200, json={"data": [phoenix_trace(2)]}),
        ]
    )
    spans = httpx2_mock.get(SPANS).mock(
        side_effect=[
            MockResponse(200, json={"data": [phoenix_span(1)], "next_cursor": "s2"}),
            MockResponse(200, json={"data": [phoenix_span(11)]}),
            MockResponse(200, json={"data": [phoenix_span(2)]}),
        ]
    )
    found = await client().session_spans("research-agent", None, "sess-9")
    assert [s.trace_id for s in found] == [f"{1:032x}", f"{11:032x}", f"{2:032x}"]
    first = traces.calls[0].request
    assert first.headers["authorization"] == "Bearer pk"
    assert dict(first.url.params) == {
        "sort": "start_time",
        "order": "asc",
        "limit": "25",
        "session_identifier": "sess-9",
    }
    assert traces.calls[1].request.url.params["cursor"] == "c2"
    asked = [call.request.url.params for call in spans.calls]
    assert asked[0].get_list("trace_id") == [f"{1:032x}"] and asked[0]["limit"] == "1000"
    assert asked[1]["cursor"] == "s2"
    assert asked[2].get_list("trace_id") == [f"{2:032x}"]
    span = found[0]
    assert span.span_id == f"{1:016x}" and span.parent_span_id is None
    assert span.attributes["openinference.span.kind"] == "LLM"
    assert span.attributes["session.id"] == "sess-9"
    assert span.attributes["llm.input_messages.0.message.role"] == "user"
    assert span.attributes["tags"] == ["a", "b"]
    assert span.status_code == "OK" and span.events[0]["name"] == "exception"


async def test_trace_pages_yield_ids_and_start_times_oldest_first(httpx2_mock):
    route = httpx2_mock.get(TRACES).mock(
        side_effect=[
            MockResponse(
                200,
                json={
                    "data": [
                        phoenix_trace(1, "2026-10-01T00:00:00+00:00"),
                        phoenix_trace(2, "2026-10-01T00:05:00+00:00"),
                    ],
                    "next_cursor": "c2",
                },
            ),
            MockResponse(200, json={"data": [phoenix_trace(3, "2026-10-01T00:10:00+00:00")]}),
        ]
    )
    since = datetime(2026, 10, 1, tzinfo=UTC)
    pages = [
        p
        async for p in client().trace_pages(
            "research-agent", None, since, since + timedelta(days=1)
        )
    ]
    assert [[tid for tid, _ in p.traces] for p in pages] == [
        [f"{1:032x}", f"{2:032x}"],
        [f"{3:032x}"],
    ]
    assert [p.last_start.minute for p in pages] == [5, 10]
    params = route.calls[0].request.url.params
    assert params["start_time"] == "2026-10-01T00:00:00+00:00"
    assert params["end_time"] == "2026-10-02T00:00:00+00:00"
    assert "include_spans" not in params and "session_identifier" not in params


async def test_trace_pages_stop_on_an_empty_page(httpx2_mock):
    route = httpx2_mock.get(TRACES).mock(
        return_value=MockResponse(200, json={"data": [], "next_cursor": "again"})
    )
    since = datetime(2026, 10, 1, tzinfo=UTC)
    pages = [
        p
        async for p in client().trace_pages(
            "research-agent", None, since, since + timedelta(days=1)
        )
    ]
    assert pages == [] and route.call_count == 1


def test_client_for_picks_the_provider():
    assert isinstance(client_for(TraceProvider.PHOENIX, "u", None, 25), PhoenixClient)


async def test_check_project_passes_a_known_project(httpx2_mock):
    route = httpx2_mock.get(f"{PHOENIX}/v1/projects/research-agent").mock(
        return_value=MockResponse(200, json={"data": {"name": "research-agent"}})
    )
    await client().check_project("research-agent")
    assert route.calls[0].request.headers["authorization"] == "Bearer pk"


async def test_check_project_refuses_an_unknown_project(httpx2_mock):
    httpx2_mock.get(f"{PHOENIX}/v1/projects/nope").mock(return_value=MockResponse(404))
    with pytest.raises(UnknownProject, match="project nope not found"):
        await client().check_project("nope")


async def test_check_project_raises_on_a_server_error(httpx2_mock):
    httpx2_mock.get(f"{PHOENIX}/v1/projects/ra").mock(return_value=MockResponse(500))
    with pytest.raises(httpx2.HTTPStatusError):
        await client().check_project("ra")


def test_span_kind_is_put_back_when_phoenix_moved_it_out_of_the_attributes():
    span = phoenix_span(1)
    del span["attributes"]["openinference.span.kind"]
    span["span_kind"] = "TOOL"
    assert _to_raw(span).attributes["openinference.span.kind"] == "TOOL"
    span["span_kind"] = "UNKNOWN"
    assert "openinference.span.kind" not in _to_raw(span).attributes
    assert _to_raw(phoenix_span(2)).attributes["openinference.span.kind"] == "LLM"


async def test_a_trace_filter_goes_to_phoenix_and_a_refused_one_says_why(httpx2_mock):
    httpx2_mock.get(VERSION).mock(return_value=MockResponse(200, text="20.12.0"))
    checked = httpx2_mock.get(TRACES).mock(
        side_effect=[
            MockResponse(400, json={"detail": "unknown name: nmae; did you mean name?"}),
            MockResponse(200, json={"data": []}),
            MockResponse(200, json={"data": [phoenix_trace(1)]}),
        ]
    )
    phoenix = client()
    await phoenix.check_filter("research-agent", None)
    with pytest.raises(BadTraceFilter, match="did you mean name"):
        await phoenix.check_filter("research-agent", "nmae == 'x'")
    await phoenix.check_filter("research-agent", "latency_ms > 1000")
    assert checked.calls[1].request.url.params["filter"] == "latency_ms > 1000"
    since = datetime(2026, 10, 1, tzinfo=UTC)
    pages = [
        p
        async for p in phoenix.trace_pages(
            "research-agent", "error_count > 0", since, since + timedelta(days=1)
        )
    ]
    assert len(pages) == 1
    assert checked.calls[2].request.url.params["filter"] == "error_count > 0"


@pytest.mark.parametrize("running", ["20.11.3", "20.3.0", "19.0.0"])
async def test_a_trace_filter_is_refused_on_a_phoenix_that_would_ignore_it(httpx2_mock, running):
    asked = httpx2_mock.get(VERSION).mock(return_value=MockResponse(200, text=running))
    with pytest.raises(BadTraceFilter, match=f"20.12.0 or later; this server runs {running}"):
        await client().check_filter("research-agent", "error_count > 0")
    assert asked.call_count == 1 and len(httpx2_mock.calls) == 1


@pytest.mark.parametrize("running", ["20.12.0", "20.12.1rc1", "21.0.0\n"])
async def test_a_trace_filter_is_probed_on_a_phoenix_that_reads_it(httpx2_mock, running):
    httpx2_mock.get(VERSION).mock(return_value=MockResponse(200, text=running))
    probe = httpx2_mock.get(TRACES).mock(return_value=MockResponse(200, json={"data": []}))
    await client().check_filter("research-agent", "error_count > 0")
    assert probe.call_count == 1
    asked_from = datetime.fromisoformat(probe.calls[0].request.url.params["start_time"])
    assert timedelta(minutes=59) < datetime.now(UTC) - asked_from < timedelta(minutes=61)


async def test_a_phoenix_429_is_rate_limited_and_a_failing_filter_probe_raises(httpx2_mock):
    httpx2_mock.get(VERSION).mock(return_value=MockResponse(200, text="21.0.0"))
    httpx2_mock.get(TRACES).mock(
        side_effect=[
            MockResponse(429, headers={"Retry-After": "5"}),
            MockResponse(500),
        ]
    )
    since = datetime(2026, 10, 1, tzinfo=UTC)
    with pytest.raises(RateLimited) as raised:
        _ = [p async for p in client().trace_pages("research-agent", None, since, since)]
    assert raised.value.retry_after == 5.0
    with pytest.raises(httpx2.HTTPStatusError):
        await client().check_filter("research-agent", "error_count > 0")
