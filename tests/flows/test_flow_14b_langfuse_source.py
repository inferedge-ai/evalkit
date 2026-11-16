import json
from datetime import datetime

import httpx2
import pytest
from cryptography.fernet import Fernet
from respx import MockResponse, Router

from evalkit.api.access import build_tenant_stores
from evalkit.api.session_pull import ServerSessionPuller
from evalkit.db.crypto import SecretBox
from tests.api.native_api import login_as
from tests.core.traces.test_langfuse_source import OBS, OTEL_TRACE, ROOTS
from tests.fake_agents import reference_mapping
from tests.flows.helpers_12_17 import DEV, flow_api
from tests.flows.test_flow_14_import_from_trace_source import settled

LANGFUSE = "https://lf.example.com"
OBSERVATIONS = f"{LANGFUSE}/api/public/v2/observations"
PROJECTS = f"{LANGFUSE}/api/public/projects"
AGENT = f"deployed@{DEV}"
TRACE_FILTER = json.dumps(
    [{"type": "string", "column": "traceName", "operator": "=", "value": "x"}]
)
SETTINGS = {"trace_source": "lf", "project": "e2e-test", "trace_filter": TRACE_FILTER}
RANGE = {"since": "2026-11-16T00:00:00Z", "until": "2026-11-17T00:00:00Z"}


@pytest.fixture
async def api(tmp_path, session_factory, stub_judges):
    async with flow_api(tmp_path, session_factory) as (client, _env):
        client.app.state.box = SecretBox(Fernet.generate_key().decode())
        source = {"name": "lf", "provider": "langfuse", "url": LANGFUSE, "api_key": "pk:sk"}
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


def serve_langfuse(router: Router) -> list[list[dict]]:
    asked: list[list[dict]] = []
    router.get(PROJECTS).mock(
        return_value=MockResponse(200, json={"data": [{"id": "p", "name": "e2e-test"}]})
    )

    def observations(request: httpx2.Request) -> MockResponse:
        conditions = json.loads(request.url.params["filter"])
        asked.append(conditions)
        columns = {c["column"]: c for c in conditions}
        if "traceId" in columns:
            data = [o for o in OBS if o["traceId"] in columns["traceId"]["value"]]
        elif "sessionId" in columns:
            data = [r for r in ROOTS if r["traceId"] == OTEL_TRACE]
        else:
            data = ROOTS
        return MockResponse(200, json={"data": data, "meta": {}})

    router.get(OBSERVATIONS).mock(side_effect=observations)
    return asked


@pytest.mark.parametrize("key", [None, "pk-only", "a:b:c"])
async def test_a_langfuse_source_needs_both_keys_joined_by_a_colon(api, key):
    body = {"name": "bad", "provider": "langfuse", "url": LANGFUSE, "api_key": key}
    refused = await api.post("/api/trace-sources", json=body)
    assert refused.status_code == 422 and "joined with" in refused.text
    edited = await api.patch("/api/trace-sources/lf", json={"api_key": "pk-only"})
    assert edited.status_code == 422 and "joined with" in edited.json()["detail"]


async def test_agent_settings_check_the_trace_filter(httpx2_mock, api):
    httpx2_mock.get(PROJECTS).mock(
        return_value=MockResponse(200, json={"data": [{"id": "p", "name": "e2e-test"}]})
    )
    checked = httpx2_mock.get(OBSERVATIONS).mock(
        side_effect=[
            MockResponse(200, json={"data": [], "meta": {}}),
            MockResponse(400, json={"message": "Invalid filter column: nope"}),
        ]
    )
    saved = await api.patch(f"/api/agents/{AGENT}", json={"settings": SETTINGS})
    assert saved.status_code == 200, saved.text
    assert saved.json()["settings"]["trace_filter"] == TRACE_FILTER
    reserved = json.dumps(
        [{"type": "string", "column": "sessionId", "operator": "=", "value": "x"}]
    )
    refused = await api.patch(
        f"/api/agents/{AGENT}", json={"settings": {**SETTINGS, "trace_filter": reserved}}
    )
    assert refused.status_code == 400 and "sessionId" in refused.json()["detail"]
    unknown = json.dumps([{"type": "string", "column": "nope", "operator": "=", "value": "x"}])
    refused_by_langfuse = await api.patch(
        f"/api/agents/{AGENT}", json={"settings": {**SETTINGS, "trace_filter": unknown}}
    )
    assert refused_by_langfuse.status_code == 400
    assert "Invalid filter column: nope" in refused_by_langfuse.json()["detail"]
    assert checked.call_count == 2
    lone = await api.patch(f"/api/agents/{AGENT}", json={"settings": {"trace_filter": "[]"}})
    assert lone.status_code == 422


async def test_phoenix_refuses_a_trace_filter(httpx2_mock, api):
    phx = {"name": "phx", "provider": "phoenix", "url": "https://phx.example.com", "api_key": "k"}
    assert (await api.post("/api/trace-sources", json=phx)).status_code == 200
    settings = {"trace_source": "phx", "project": "p", "trace_filter": "[]"}
    refused = await api.patch(f"/api/agents/{AGENT}", json={"settings": settings})
    assert refused.status_code == 400
    assert refused.json()["detail"] == "Phoenix trace filters are not supported yet"


async def test_a_range_import_walks_newest_first_with_the_agent_filter(httpx2_mock, api):
    asked = serve_langfuse(httpx2_mock)
    assert (await api.patch(f"/api/agents/{AGENT}", json={"settings": SETTINGS})).status_code == 200
    started = await api.post(f"/api/agents/{AGENT}/imports", json=RANGE)
    assert started.status_code == 202, started.text
    assert started.json()["resume_from"] == "2026-11-17T00:00:00Z"
    assert started.json()["trace_filter"] == TRACE_FILTER
    view = await settled(api, started.json()["id"])
    assert view["status"] == "done" and view["progress"] == 1.0
    assert view["traces"] == len(ROOTS)
    oldest = min(datetime.fromisoformat(r["startTime"]) for r in ROOTS)
    assert datetime.fromisoformat(view["resume_from"]) == oldest
    roots = asked[0]
    assert roots[0] == json.loads(TRACE_FILTER)[0]
    assert {
        "type": "boolean",
        "column": "isRootObservation",
        "operator": "=",
        "value": True,
    } in roots


async def test_a_session_import_with_a_filter_needs_write_and_the_puller_sends_the_filter(
    httpx2_mock, api
):
    asked = serve_langfuse(httpx2_mock)
    assert (await api.patch(f"/api/agents/{AGENT}", json={"settings": SETTINGS})).status_code == 200
    alice = await login_as(api, "alice", "password")
    refused = await alice.post(
        f"/api/agents/{AGENT}/sessions/import",
        json={"session_id": "otel-session-1", "trace_filter": "[]"},
    )
    assert refused.status_code == 403
    asked.clear()
    own = await alice.post(
        f"/api/agents/{AGENT}/sessions/import", json={"session_id": "otel-session-1"}
    )
    assert own.status_code == 200 and own.json()["imported"] == 4
    assert len(asked) == 2 and "sessionId" in {c["column"] for c in asked[0]}
    stores = build_tenant_stores(api.app.state, "default")
    puller = ServerSessionPuller(stores.agents, stores.sources, stores.mappings, stores.spans, 25)
    asked.clear()
    await puller(AGENT, "otel-session-1")
    assert asked[0][0] == json.loads(TRACE_FILTER)[0]
    await alice.aclose()


async def test_imports_naming_the_agents_own_source_keep_its_filter(httpx2_mock, api):
    asked = serve_langfuse(httpx2_mock)
    assert (await api.patch(f"/api/agents/{AGENT}", json={"settings": SETTINGS})).status_code == 200
    asked.clear()
    target = {"source": "lf", "project": "e2e-test"}
    started = await api.post(f"/api/agents/{AGENT}/imports", json={**target, **RANGE})
    assert started.status_code == 202, started.text
    assert started.json()["trace_filter"] == TRACE_FILTER
    await settled(api, started.json()["id"])
    session = await api.post(
        f"/api/agents/{AGENT}/sessions/import", json={**target, "session_id": "otel-session-1"}
    )
    assert session.status_code == 200, session.text
    roots = [c for c in asked if any(x["column"] == "isRootObservation" for x in c)]
    assert roots and all(c[0] == json.loads(TRACE_FILTER)[0] for c in roots)
