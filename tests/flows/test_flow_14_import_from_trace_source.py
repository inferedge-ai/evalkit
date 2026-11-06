import asyncio

import httpx2
import pytest
from cryptography.fernet import Fernet
from respx import MockResponse, Route, Router
from sqlalchemy import func, select

from evalkit.cli.traces_commands import render_check
from evalkit.db.crypto import SecretBox
from evalkit.db.models import Span
from tests.api.native_api import login_as
from tests.core.traces.test_phoenix_source import phoenix_span, phoenix_trace
from tests.fake_agents import reference_mapping
from tests.flows.helpers_12_17 import DEV, flow_api

PHOENIX = "https://phoenix.example.com"
TRACES = f"{PHOENIX}/v1/projects/research-agent/traces"
SPANS = f"{PHOENIX}/v1/projects/research-agent/spans"
AGENT = f"deployed@{DEV}"
NO_SESSION = 7


def serve_spans(router: Router) -> Route:
    def spans(request: httpx2.Request) -> MockResponse:
        ids = [int(tid, 16) for tid in request.url.params.get_list("trace_id")]
        data = [phoenix_span(n, None if n == NO_SESSION else f"sess-{n}") for n in ids]
        return MockResponse(200, json={"data": data})

    return router.get(SPANS).mock(side_effect=spans)


@pytest.fixture
async def api(tmp_path, session_factory, stub_judges):
    async with flow_api(tmp_path, session_factory) as (client, _env):
        client.app.state.box = SecretBox(Fernet.generate_key().decode())
        source = {"name": "phx", "provider": "phoenix", "url": PHOENIX, "api_key": "pk"}
        assert (await client.post("/api/trace-sources", json=source)).status_code == 200
        created = await client.post(
            "/api/agents",
            json={
                "workspace": DEV,
                "name": "deployed",
                "trajectory_source": "otel",
                "mapping": reference_mapping(),
            },
        )
        assert created.status_code == 200, created.text
        yield client


def body(session_id: str = "sess-9", source: str = "phx") -> dict:
    return {"source": source, "project": "research-agent", "session_id": session_id}


async def span_rows(api) -> list[tuple[str, int | None]]:
    async with api.app.state.session_factory() as session:
        rows = await session.execute(select(Span.session_id, Span.external_trace_source_id))
        return [tuple(r) for r in rows.all()]


async def test_flow_14_import_tags_spans_is_idempotent_and_checks(httpx2_mock, api):
    route = httpx2_mock.get(TRACES).mock(
        return_value=MockResponse(200, json={"data": [phoenix_trace(9)]})
    )
    serve_spans(httpx2_mock)
    first = await api.post(f"/api/agents/{AGENT}/sessions/import", json=body())
    assert first.status_code == 200 and first.json() == {"imported": 1}
    assert route.calls[0].request.headers["authorization"] == "Bearer pk"
    again = await api.post(f"/api/agents/{AGENT}/sessions/import", json=body())
    assert again.json() == {"imported": 1}
    sources = (await api.get("/api/trace-sources")).json()
    assert [(s["name"], s["provider"]) for s in sources] == [("phx", "phoenix")]
    assert "pk" not in str(sources)
    rows = await span_rows(api)
    assert rows == [("sess-9", sources[0]["id"])]
    session = (
        await api.get(f"/api/agents/{AGENT}/sessions/sess-9/trajectory", params={"esf": "true"})
    ).json()
    lines, _ = render_check(session)
    assert [line.split()[1] for line in lines] == [
        "session",
        "root",
        "mapping",
        "llm",
        "tools",
        "unmapped",
        "trajectory",
    ]
    async with api.app.state.session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(Span)) == 1


async def test_flow_14_session_id_comes_from_the_mapping_else_the_command_line(httpx2_mock, api):
    httpx2_mock.get(TRACES).mock(
        side_effect=[
            MockResponse(200, json={"data": [phoenix_trace(9)]}),
            MockResponse(200, json={"data": [phoenix_trace(NO_SESSION)]}),
        ]
    )
    serve_spans(httpx2_mock)
    await api.post(f"/api/agents/{AGENT}/sessions/import", json=body("given-1"))
    await api.post(f"/api/agents/{AGENT}/sessions/import", json=body("given-2"))
    assert sorted(r[0] for r in await span_rows(api)) == ["given-2", "sess-9"]


async def test_flow_14_errors(httpx2_mock, api):
    unknown_source = await api.post(f"/api/agents/{AGENT}/sessions/import", json=body(source="x"))
    assert unknown_source.status_code == 404
    assert "unknown trace source" in unknown_source.json()["detail"]
    unknown_agent = await api.post(f"/api/agents/ghost@{DEV}/sessions/import", json=body())
    assert unknown_agent.status_code == 404
    alice = await login_as(api, "alice", "password")
    assert (
        await alice.post(f"/api/agents/{AGENT}/sessions/import", json=body())
    ).status_code == 403
    httpx2_mock.get(TRACES).mock(return_value=MockResponse(500))
    down = await api.post(f"/api/agents/{AGENT}/sessions/import", json=body())
    assert down.status_code == 502 and down.json()["detail"].startswith("phx: ")
    httpx2_mock.get(TRACES).mock(return_value=MockResponse(200, json={"data": []}))
    empty = await api.post(f"/api/agents/{AGENT}/sessions/import", json=body("none"))
    assert empty.json() == {"imported": 0}
    session = (
        await api.get(f"/api/agents/{AGENT}/sessions/none/trajectory", params={"esf": "true"})
    ).json()
    _, ok = render_check(session)
    assert ok is False
    akshat = await login_as(api, "akshat", "password")
    await akshat.post(
        "/api/agents",
        json={
            "workspace": "akshat",
            "name": "traced",
            "trajectory_source": "otel",
            "mapping": reference_mapping(),
        },
    )
    assert (
        await api.post("/api/agents/traced@akshat/sessions/import", json=body())
    ).status_code == 403
    await alice.aclose()
    await akshat.aclose()


def trace_page(starts: list[int], cursor: str | None = None) -> dict:
    data = [phoenix_trace(n, f"2026-10-01T{n:02d}:00:00+00:00") for n in starts]
    return {"data": data, **({"next_cursor": cursor} if cursor else {})}


RANGE = {
    "source": "phx",
    "project": "research-agent",
    "since": "2026-10-01T00:00:00Z",
    "until": "2026-10-02T00:00:00Z",
}


async def settled(api, import_id: int) -> dict:
    for _ in range(500):
        view = (await api.get(f"/api/agents/{AGENT}/imports/{import_id}")).json()
        if view["status"] not in ("running", "stopping"):
            return view
        await asyncio.sleep(0.01)
    raise AssertionError(f"import {import_id} still {view['status']}")


async def test_flow_14_range_import_runs_to_done(httpx2_mock, api):
    serve_spans(httpx2_mock)
    route = httpx2_mock.get(TRACES).mock(
        side_effect=[
            MockResponse(200, json=trace_page([1, 2], cursor="c2")),
            MockResponse(200, json=trace_page([3])),
        ]
    )
    started = await api.post(f"/api/agents/{AGENT}/imports", json=RANGE)
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "running"
    view = await settled(api, started.json()["id"])
    assert view["status"] == "done" and view["error"] is None
    assert (view["traces"], view["spans"], view["sample_session"]) == (3, 3, "sess-3")
    assert view["resume_from"] == "2026-10-01T03:00:00Z"
    assert view["agent"] == AGENT and view["source"] == "phx" and view["created_by"] == "admin"
    params = route.calls[0].request.url.params
    assert (params["start_time"], params["end_time"]) == (
        "2026-10-01T00:00:00+00:00",
        "2026-10-02T00:00:00+00:00",
    )
    assert params["limit"] == "25"
    rows = await span_rows(api)
    assert sorted(r[0] for r in rows) == ["sess-1", "sess-2", "sess-3"]
    assert {r[1] for r in rows} == {(await api.get("/api/trace-sources")).json()[0]["id"]}
    listed = (await api.get(f"/api/agents/{AGENT}/imports")).json()
    assert [i["id"] for i in listed] == [view["id"]]


async def test_flow_14_stop_then_continue_from_resume_from(httpx2_mock, api):
    serve_spans(httpx2_mock)
    reached, release = asyncio.Event(), asyncio.Event()

    async def held(request):
        reached.set()
        await release.wait()
        return MockResponse(200, json=trace_page([2], cursor="c3"))

    route = httpx2_mock.get(TRACES).mock(
        side_effect=[
            MockResponse(200, json=trace_page([1], cursor="c2")),
            held,
            MockResponse(200, json=trace_page([2, 5])),
        ]
    )
    import_id = (await api.post(f"/api/agents/{AGENT}/imports", json=RANGE)).json()["id"]
    await asyncio.wait_for(reached.wait(), 5)
    stopping = await api.post(f"/api/agents/{AGENT}/imports/{import_id}/stop")
    assert stopping.json()["status"] == "stopping"
    again = await api.post(f"/api/agents/{AGENT}/imports/{import_id}/stop")
    assert again.status_code == 409
    release.set()
    view = await settled(api, import_id)
    assert (view["status"], view["traces"]) == ("stopped", 2)
    assert view["resume_from"] == "2026-10-01T02:00:00Z"
    continued = await api.post(f"/api/agents/{AGENT}/imports/{import_id}/continue")
    assert continued.json()["status"] == "running"
    view = await settled(api, import_id)
    assert (view["status"], view["traces"], view["spans"]) == ("done", 3, 3)
    assert route.calls[2].request.url.params["start_time"] == "2026-10-01T02:00:00+00:00"


async def test_flow_14_failed_import_records_the_error(httpx2_mock, api):
    serve_spans(httpx2_mock)
    httpx2_mock.get(TRACES).mock(return_value=MockResponse(500))
    import_id = (await api.post(f"/api/agents/{AGENT}/imports", json=RANGE)).json()["id"]
    view = await settled(api, import_id)
    assert view["status"] == "failed" and view["error"].startswith("HTTPStatusError")
    httpx2_mock.get(TRACES).mock(return_value=MockResponse(200, json=trace_page([4])))
    assert (await api.post(f"/api/agents/{AGENT}/imports/{import_id}/continue")).status_code == 200
    assert (await settled(api, import_id))["status"] == "done"


async def test_flow_14_range_import_rules(httpx2_mock, api):
    serve_spans(httpx2_mock)
    url = f"/api/agents/{AGENT}/imports"
    backwards = {**RANGE, "since": RANGE["until"], "until": RANGE["since"]}
    assert (await api.post(url, json=backwards)).status_code == 422
    naive = {**RANGE, "since": "2026-10-01T00:00:00"}
    assert (await api.post(url, json=naive)).status_code == 422
    assert (await api.post(url, json={**RANGE, "source": "x"})).status_code == 404
    alice = await login_as(api, "alice", "password")
    assert (await alice.post(url, json=RANGE)).status_code == 403
    await alice.aclose()
    release = asyncio.Event()

    async def held(request):
        await release.wait()
        return MockResponse(200, json=trace_page([1]))

    httpx2_mock.get(TRACES).mock(side_effect=held)
    first = (await api.post(url, json=RANGE)).json()["id"]
    second = await api.post(url, json=RANGE)
    assert second.status_code == 409 and f"import {first} is already running" in second.text
    release.set()
    await settled(api, first)
    no_session = {"schema_version": "1"}
    pushed = (await api.put(f"/api/agents/{AGENT}/mappings/draft", json=no_session)).json()
    await api.post(f"/api/agents/{AGENT}/mappings/{pushed['version']}/activate")
    refused = await api.post(url, json=RANGE)
    assert refused.status_code == 409 and "session_id" in refused.json()["detail"]
    atif = await api.post("/api/agents", json={"workspace": DEV, "name": "harnessed"})
    assert atif.status_code == 200, atif.text
    for path, payload in (
        (f"/api/agents/harnessed@{DEV}/imports", RANGE),
        (f"/api/agents/harnessed@{DEV}/sessions/import", body()),
    ):
        wrong_kind = await api.post(path, json=payload)
        assert wrong_kind.status_code == 400 and "only OTel agents" in wrong_kind.json()["detail"]


async def test_flow_14_private_source_urls_are_refused_unless_allowed(api):
    metadata = {"name": "meta", "provider": "phoenix", "url": "http://169.254.169.254/"}
    local = {"url": "http://127.0.0.1:6006"}
    api.app.state.cfg.allow_private_endpoints = False
    created = await api.post("/api/trace-sources", json=metadata)
    assert created.status_code == 400 and "allow_private_endpoints" in created.json()["detail"]
    moved = await api.patch("/api/trace-sources/phx", json=local)
    assert moved.status_code == 400 and "allow_private_endpoints" in moved.json()["detail"]
    malformed = await api.post("/api/trace-sources", json={**metadata, "url": "http://[::1"})
    assert malformed.status_code == 400 and "http://[::1" in malformed.json()["detail"]
    sources = (await api.get("/api/trace-sources")).json()
    assert [(s["name"], s["url"]) for s in sources] == [("phx", PHOENIX)]
    api.app.state.cfg.allow_private_endpoints = True
    assert (await api.post("/api/trace-sources", json=metadata)).status_code == 200
    assert (await api.patch("/api/trace-sources/phx", json=local)).json()["url"] == local["url"]


async def test_flow_14_imports_refuse_a_stored_private_url(httpx2_mock, api):
    httpx2_mock.get(TRACES).mock(return_value=MockResponse(500))
    import_id = (await api.post(f"/api/agents/{AGENT}/imports", json=RANGE)).json()["id"]
    assert (await settled(api, import_id))["status"] == "failed"
    await api.patch("/api/trace-sources/phx", json={"url": "http://127.0.0.1:6006"})
    api.app.state.cfg.allow_private_endpoints = False
    for path, payload in (
        (f"/api/agents/{AGENT}/sessions/import", body()),
        (f"/api/agents/{AGENT}/imports", RANGE),
        (f"/api/agents/{AGENT}/imports/{import_id}/continue", None),
    ):
        refused = await api.post(path, json=payload)
        assert refused.status_code == 400 and "allow_private_endpoints" in refused.json()["detail"]
    imports = (await api.get(f"/api/agents/{AGENT}/imports")).json()
    assert [(i["id"], i["status"]) for i in imports] == [(import_id, "failed")]
