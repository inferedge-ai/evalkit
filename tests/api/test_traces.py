import json

import pytest
from cryptography.fernet import Fernet

from evalkit.client.api import AsyncApi
from evalkit.client.sessions import HttpSessionReader
from evalkit.contracts.enums import TrajectorySource
from evalkit.core.traces.poll import poll_trajectory
from evalkit.core.traces.sessions import SessionTooLarge
from evalkit.db.crypto import SecretBox
from evalkit.db.trace_sources import TraceSourceStore
from tests.api.native_api import api_env, login_as
from tests.api.test_otel import PROTO, otlp_payload
from tests.fake_agents import reference_mapping
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent

DOC = {
    "schema_version": "1",
    "kind": {"attribute": "openinference.span.kind", "values": {"LLM": "llm", "CHAIN": "chain"}},
    "model": {"attribute": "llm.model_name"},
}


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        await seed_agent(
            session_factory,
            "sdk",
            "admin",
            harness_url=None,
            trajectory_source=TrajectorySource.OTEL,
            mapping=reference_mapping(),
        )
        headers = {**PROTO, "X-Evalkit-Agent": "sdk"}
        await client.post("/api/otel/v1/traces", content=otlp_payload(), headers=headers)
        yield client


async def test_mapping_lifecycle_and_previews(api):
    listed = (await api.get("/api/agents/sdk@admin/mappings")).json()
    assert [(m["version"], m["status"]) for m in listed] == [(1, "active")]
    no_draft = await api.get(
        "/api/agents/sdk@admin/sessions/sess-1/trajectory", params={"mapping": "draft"}
    )
    assert no_draft.status_code == 404 and "no draft mapping" in no_draft.json()["detail"]
    bad_doc = {"schema_version": "1", "kind": {"attr": "x"}}
    bad = await api.put("/api/agents/sdk@admin/mappings/draft", json=bad_doc)
    assert bad.status_code == 400 and "attribute" in bad.json()["detail"]
    draft = (await api.put("/api/agents/sdk@admin/mappings/draft", json=DOC)).json()
    assert draft["version"] == 2 and draft["status"] == "draft"
    preview = (
        await api.get(
            "/api/agents/sdk@admin/sessions/sess-1/trajectory",
            params={"mapping": "draft", "esf": "true"},
        )
    ).json()
    assert preview["mapping_version"] == 2
    assert [s["kind"] for s in preview["esf"]] == ["chain", "llm"]
    before = (await api.get("/api/agents/sdk@admin/sessions/sess-1/trajectory")).json()
    assert before["mapping_version"] == 1
    activated = (await api.post("/api/agents/sdk@admin/mappings/2/activate")).json()
    assert activated["status"] == "active"
    after = (await api.get("/api/agents/sdk@admin/sessions/sess-1/trajectory")).json()
    assert after["mapping_version"] == 2
    pushed = (
        await api.put("/api/agents/sdk@admin/mappings/draft", params={"activate": "true"}, json=DOC)
    ).json()
    assert pushed["version"] == 3 and pushed["status"] == "active"
    assert (await api.post("/api/agents/sdk@admin/mappings/9/activate")).status_code == 404
    assert (await api.get("/api/agents/nope@admin/sessions/x/trajectory")).status_code == 404


def with_encryption(api) -> None:
    box = SecretBox(Fernet.generate_key().decode())
    api.app.state.trace_sources = TraceSourceStore(api.app.state.session_factory, box)


async def test_trace_sources_are_admin_rows_and_hide_keys(api):
    body = {"name": "phx", "provider": "phoenix", "url": "u", "api_key": "secret"}
    refused = await api.post("/api/trace-sources", json=body)
    assert refused.status_code == 400 and "encryption_key" in refused.json()["detail"]
    with_encryption(api)
    alice = await login_as(api, "alice", "password")
    assert (await alice.post("/api/trace-sources", json=body)).status_code == 403
    assert (await api.post("/api/trace-sources", json=body)).status_code == 200
    listed = (await alice.get("/api/trace-sources")).json()
    assert [(s["name"], s["provider"], s["url"]) for s in listed] == [("phx", "phoenix", "u")]
    assert "secret" not in json.dumps(listed)
    assert (await api.get("/api/meta")).json()["trace_sources"] == ["phx"]
    await alice.aclose()


async def test_trajectory_response_is_the_session_model_as_json(api):
    api.app.state.cfg.otel.settle_seconds = 3600
    reader = api.app.state.sessions
    for esf in (False, True):
        expected = await reader.read("sdk@admin", "sess-1", include_esf=esf)
        resp = await api.get(
            "/api/agents/sdk@admin/sessions/sess-1/trajectory", params={"esf": str(esf).lower()}
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        assert resp.json() == expected.model_dump(mode="json")
    spans = (await api.get("/api/agents/sdk@admin/sessions/sess-1/spans")).json()
    record = await reader.agent("sdk@admin")
    stored = await api.app.state.spans.session_spans(record.id, "sess-1")
    assert spans == [s.model_dump(mode="json") for s in stored]
    assert (await api.get("/api/agents/nope@admin/sessions/x/spans")).status_code == 404


async def test_session_over_the_size_cap_is_refused_with_413(api):
    api.app.state.cfg.otel.max_session_bytes = 10
    resp = await api.get("/api/agents/sdk@admin/sessions/sess-1/trajectory", params={"esf": "true"})
    assert resp.status_code == 413
    detail = resp.json()["detail"]
    assert "session sess-1 of agent sdk@admin holds" in detail
    assert "otel.max_session_bytes (0.0 MiB)" in detail
    spans = await api.get("/api/agents/sdk@admin/sessions/sess-1/spans")
    assert spans.status_code == 200 and len(spans.json()) == 2
    token = api.headers["Authorization"].removeprefix("Bearer ")
    client = AsyncApi("http://test", token, transport=api._transport)
    sessions = HttpSessionReader(client)
    with pytest.raises(SessionTooLarge, match="otel.max_session_bytes"):
        await sessions.trajectory("sdk@admin", "sess-1")
    assert await poll_trajectory(sessions, "sdk@admin", "sess-1", 60) == (None, "unavailable")
    await client.aclose()
