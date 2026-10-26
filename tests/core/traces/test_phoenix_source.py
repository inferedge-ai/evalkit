from datetime import UTC, datetime, timedelta

import httpx
import respx

from evalkit.contracts.enums import TraceProvider
from evalkit.core.traces.sources import client_for
from evalkit.core.traces.sources.phoenix import PhoenixClient, flatten_attributes

PHOENIX = "https://phoenix.example.com"
TRACES = f"{PHOENIX}/v1/projects/research-agent/traces"
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


@respx.mock
async def test_session_spans_lists_the_traces_then_reads_their_spans_on_every_page():
    traces = respx.get(TRACES).mock(
        side_effect=[
            httpx.Response(200, json={"data": [phoenix_trace(1)], "next_cursor": "c2"}),
            httpx.Response(200, json={"data": [phoenix_trace(2)]}),
        ]
    )
    spans = respx.get(SPANS).mock(
        side_effect=[
            httpx.Response(200, json={"data": [phoenix_span(1)], "next_cursor": "s2"}),
            httpx.Response(200, json={"data": [phoenix_span(11)]}),
            httpx.Response(200, json={"data": [phoenix_span(2)]}),
        ]
    )
    found = await client().session_spans("research-agent", "sess-9")
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


@respx.mock
async def test_trace_pages_yield_ids_and_start_times_oldest_first():
    route = respx.get(TRACES).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "data": [
                        phoenix_trace(1, "2026-10-01T00:00:00+00:00"),
                        phoenix_trace(2, "2026-10-01T00:05:00+00:00"),
                    ],
                    "next_cursor": "c2",
                },
            ),
            httpx.Response(200, json={"data": [phoenix_trace(3, "2026-10-01T00:10:00+00:00")]}),
        ]
    )
    since = datetime(2026, 10, 1, tzinfo=UTC)
    pages = [
        p async for p in client().trace_pages("research-agent", since, since + timedelta(days=1))
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


@respx.mock
async def test_trace_pages_stop_on_an_empty_page():
    route = respx.get(TRACES).mock(
        return_value=httpx.Response(200, json={"data": [], "next_cursor": "again"})
    )
    since = datetime(2026, 10, 1, tzinfo=UTC)
    pages = [
        p async for p in client().trace_pages("research-agent", since, since + timedelta(days=1))
    ]
    assert pages == [] and route.call_count == 1


def test_client_for_picks_the_provider():
    assert isinstance(client_for(TraceProvider.PHOENIX, "u", None, 25), PhoenixClient)
