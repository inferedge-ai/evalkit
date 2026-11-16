import json
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
from respx import MockResponse

from evalkit.contracts.enums import TraceProvider
from evalkit.contracts.esf import MappingDocument
from evalkit.core.retry import Retry
from evalkit.core.traces.convert import to_atif
from evalkit.core.traces.mapping import to_esf
from evalkit.core.traces.presets import read_preset
from evalkit.core.traces.sources import (
    BadTraceFilter,
    RateLimited,
    UnknownProject,
    check_api_key,
    client_for,
    newest_first,
)
from evalkit.core.traces.sources.langfuse import KEY_FORMAT, LangfuseClient, to_raw

LANGFUSE = "https://lf.example.com"
OBSERVATIONS = f"{LANGFUSE}/api/public/v2/observations"
DATA = Path(__file__).parents[2] / "data" / "langfuse"
OBS = json.loads((DATA / "observations.json").read_text())["data"]
ROOTS = json.loads((DATA / "roots.json").read_text())["data"]
OTEL_TRACE = next(o["traceId"] for o in OBS if o["name"] == "invoke_agent stock-agent")
OBSERVE_TRACE = next(o["traceId"] for o in OBS if o["name"] == "observe-agent")


def client(page_traces: int = 25) -> LangfuseClient:
    return LangfuseClient(LANGFUSE, "pk-lf-1:sk-lf-2", page_traces)


def named(name: str) -> dict:
    return next(o for o in OBS if o["name"] == name)


def filter_of(call) -> list[dict]:
    return json.loads(call.request.url.params["filter"])


def test_an_otel_generation_becomes_a_span_with_langfuse_sdk_names():
    obs = next(
        o
        for o in OBS
        if o["name"] == "chat claude-sonnet-4-5" and o["usageDetails"].get("input_cached_tokens")
    )
    span = to_raw(obs)
    assert span.trace_id == OTEL_TRACE and span.parent_span_id == obs["parentObservationId"]
    attrs = span.attributes
    assert attrs["langfuse.observation.type"] == "generation"
    assert attrs["langfuse.observation.model.name"] == "claude-sonnet-4-5"
    assert json.loads(attrs["langfuse.observation.usage_details"])["input_cached_tokens"] == 800
    assert json.loads(attrs["langfuse.observation.input"])[0]["role"] == "system"
    assert attrs["gen_ai.usage.input_tokens"] == 1000
    assert "session.id" not in attrs
    assert span.resource["service.name"] == "stock-agent"
    assert span.scope == "stock-agent"
    assert span.start_time.tzinfo is not None


def test_an_observe_span_keeps_its_session_and_never_stores_the_public_key():
    span = to_raw(named("llm-call"))
    assert span.attributes["session.id"] == "observe-session-1"
    assert span.scope == "langfuse-sdk"
    assert not any("public_key" in k for k in span.attributes)
    assert not any("pk-lf" in str(v) for v in span.attributes.values())


def test_level_error_is_an_error_status():
    span = to_raw({**named("llm-call"), "level": "ERROR", "statusMessage": "boom"})
    assert (span.status_code, span.status_message) == ("ERROR", "boom")
    assert (
        to_raw({**named("llm-call"), "usageDetails": None}).attributes.get(
            "langfuse.observation.usage_details"
        )
        is None
    )


@pytest.mark.parametrize("key", [None, "", "pk-lf-1", "pk:sk:x", ":sk", "pk:"])
def test_the_key_must_be_public_and_secret_joined_by_a_colon(key):
    with pytest.raises(ValueError, match="joined with"):
        check_api_key(TraceProvider.LANGFUSE, key)
    check_api_key(TraceProvider.PHOENIX, key)
    assert KEY_FORMAT.startswith("Langfuse needs")


def test_only_langfuse_walks_newest_first():
    assert newest_first(TraceProvider.LANGFUSE) and not newest_first(TraceProvider.PHOENIX)


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("not json", "not JSON"),
        ('{"column": "name"}', "JSON array"),
        ('["x"]', "JSON array"),
        (
            '[{"type": "datetime", "column": "startTime", "operator": ">", "value": "x"}]',
            "startTime",
        ),
        (
            '[{"column": "sessionId"}, {"column": "isRootObservation"}]',
            "isRootObservation, sessionId",
        ),
    ],
)
async def test_bad_filters_are_refused_with_a_reason(value, reason):
    with pytest.raises(BadTraceFilter, match=reason):
        await client().check_filter("e2e-test", value)


async def test_a_filter_langfuse_refuses_comes_back_with_its_reason(httpx2_mock):
    route = httpx2_mock.get(OBSERVATIONS).mock(
        side_effect=[
            MockResponse(400, json={"message": "Invalid filter column: nope"}),
            MockResponse(200, json={"data": [], "meta": {}}),
        ]
    )
    bad = '[{"type": "string", "column": "nope", "operator": "=", "value": "x"}]'
    with pytest.raises(BadTraceFilter, match="Invalid filter column: nope"):
        await client().check_filter("e2e-test", bad)
    good = '[{"type": "string", "column": "traceName", "operator": "=", "value": "x"}]'
    await client().check_filter("e2e-test", good)
    assert route.calls[1].request.url.params["limit"] == "1"
    await client().check_filter("e2e-test", None)
    assert route.call_count == 2


def test_an_app_root_whose_parent_was_not_exported_still_roots_the_trajectory():
    obs = [o for o in OBS if o["traceId"] == OTEL_TRACE]
    root = next(o for o in obs if o["parentObservationId"] is None)
    orphaned = [
        {**o, "parentObservationId": "f" * 16, "isRootObservation": True} if o is root else o
        for o in obs
    ]
    mapping = MappingDocument.model_validate_json(read_preset("langfuse"))
    esf = [to_esf(to_raw(o), mapping) for o in orphaned]
    assert to_raw(next(o for o in orphaned if o["id"] == root["id"])).parent_span_id == "f" * 16
    trajectory = to_atif(esf, mapping, agent_name="a", session_id="s", mapping_version=1)
    assert trajectory.steps[-1].message == "AAPL is 101.5"


async def test_trace_pages_join_the_filter_root_and_window_and_yield_empty_pages(httpx2_mock):
    route = httpx2_mock.get(OBSERVATIONS).mock(
        side_effect=[
            MockResponse(200, json={"data": ROOTS, "meta": {"cursor": "c2"}}),
            MockResponse(200, json={"data": [], "meta": {"cursor": "c3"}}),
            MockResponse(200, json={"data": [], "meta": {}}),
        ]
    )
    user = [{"type": "string", "column": "traceName", "operator": "=", "value": "x"}]
    since = datetime(2026, 11, 16, tzinfo=UTC)
    until = datetime(2026, 11, 17, 10, 0, 0, 5000, tzinfo=UTC)
    pages = [p async for p in client(10).trace_pages("e2e-test", json.dumps(user), since, until)]
    assert [len(p.traces) for p in pages] == [2, 0, 0]
    assert pages[0].traces[0] == (
        ROOTS[0]["traceId"],
        datetime.fromisoformat(ROOTS[0]["startTime"]),
    )
    first = route.calls[0].request
    assert first.headers["authorization"].startswith("Basic ")
    assert first.url.params["fields"] == "core" and first.url.params["limit"] == "10"
    assert filter_of(route.calls[0]) == [
        *user,
        {"type": "boolean", "column": "isRootObservation", "operator": "=", "value": True},
        {
            "type": "datetime",
            "column": "startTime",
            "operator": ">=",
            "value": "2026-11-16T00:00:00.000+00:00",
        },
        {
            "type": "datetime",
            "column": "startTime",
            "operator": "<",
            "value": "2026-11-17T10:00:00.005+00:00",
        },
    ]
    assert route.calls[1].request.url.params["cursor"] == "c2"


async def test_session_spans_with_no_roots_return_nothing_after_one_request(httpx2_mock):
    route = httpx2_mock.get(OBSERVATIONS).mock(
        return_value=MockResponse(200, json={"data": [], "meta": {}})
    )
    assert await client().session_spans("e2e-test", None, "nope") == []
    assert route.call_count == 1
    assert filter_of(route.calls[0])[-1] == {
        "type": "string",
        "column": "sessionId",
        "operator": "=",
        "value": "nope",
    }


async def test_session_spans_read_every_observation_of_the_session_traces(httpx2_mock):
    otel = [o for o in OBS if o["traceId"] == OTEL_TRACE]
    route = httpx2_mock.get(OBSERVATIONS).mock(
        side_effect=[
            MockResponse(
                200, json={"data": [r for r in ROOTS if r["traceId"] == OTEL_TRACE], "meta": {}}
            ),
            MockResponse(200, json={"data": otel[:2], "meta": {"cursor": "n"}}),
            MockResponse(200, json={"data": otel[2:], "meta": {}}),
        ]
    )
    spans = await client().session_spans("e2e-test", None, "otel-session-1")
    assert len(spans) == len(otel) == 4
    assert filter_of(route.calls[1]) == [
        {"type": "stringOptions", "column": "traceId", "operator": "any of", "value": [OTEL_TRACE]}
    ]
    assert route.calls[1].request.url.params["fields"] == "core,basic,time,io,metadata,model,usage"


async def test_trace_spans_ask_for_at_most_page_traces_ids_at_a_time(httpx2_mock):
    route = httpx2_mock.get(OBSERVATIONS).mock(
        return_value=MockResponse(200, json={"data": [], "meta": {}})
    )
    ids = [f"t{n}" for n in range(5)]
    assert [b async for b in client(2).trace_spans("e2e-test", ids)] == []
    assert [filter_of(c)[0]["value"] for c in route.calls] == [["t0", "t1"], ["t2", "t3"], ["t4"]]


async def test_check_project_compares_the_key_project_name(httpx2_mock):
    httpx2_mock.get(f"{LANGFUSE}/api/public/projects").mock(
        return_value=MockResponse(200, json={"data": [{"id": "p1", "name": "e2e-test"}]})
    )
    await client().check_project("e2e-test")
    with pytest.raises(UnknownProject, match="this key reads e2e-test"):
        await client().check_project("other")


async def test_a_429_is_retried_with_the_import_policy(httpx2_mock):
    route = httpx2_mock.get(OBSERVATIONS).mock(
        side_effect=[
            MockResponse(429, headers={"Retry-After": "0"}),
            MockResponse(200, json={"data": ROOTS, "meta": {}}),
        ]
    )
    retry = Retry(total=2, status_forcelist=(429,), backoff_factor=0.0)
    lf = client_for(TraceProvider.LANGFUSE, LANGFUSE, "pk:sk", 25, retry=retry)
    since = datetime(2026, 11, 16, tzinfo=UTC)
    pages = [p async for p in lf.trace_pages("e2e-test", None, since, since)]
    assert route.call_count == 2 and len(pages[0].traces) == 2


@pytest.mark.parametrize(("header", "expected"), [({"Retry-After": "7"}, 7.0), ({}, 30.0)])
async def test_a_429_left_after_retries_is_rate_limited_with_its_wait(
    httpx2_mock, header, expected
):
    httpx2_mock.get(OBSERVATIONS).mock(return_value=MockResponse(429, headers=header))
    since = datetime(2026, 11, 16, tzinfo=UTC)
    with pytest.raises(RateLimited) as raised:
        _ = [p async for p in client().trace_pages("e2e-test", None, since, since)]
    assert raised.value.retry_after == expected
    assert isinstance(raised.value, httpx2.HTTPStatusError)
