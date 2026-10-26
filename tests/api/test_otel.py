import gzip
import json

import httpx
import pytest
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select

from evalkit.contracts.enums import TrajectorySource, WorkspaceRole
from evalkit.db.models import AuthToken
from evalkit.db.workspaces import WorkspaceStore
from tests.api.native_api import CAPS, api_env, login_as
from tests.fake_agents import reference_mapping
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent

PROTO = {"content-type": "application/x-protobuf"}


def otlp_payload(session_id="sess-1", agent_attr=None, child=True) -> bytes:
    exporter = InMemorySpanExporter()
    resource = Resource.create({"evalkit.agent": agent_attr} if agent_attr else {})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("t")
    root_attrs = {"session.id": session_id, "openinference.span.kind": "CHAIN"}
    with tracer.start_as_current_span("evalkit.run", attributes=root_attrs):
        if child:
            llm_attrs = {"openinference.span.kind": "LLM", "llm.model_name": "m"}
            with tracer.start_as_current_span("llm", attributes=llm_attrs):
                pass
    return encode_spans(exporter.get_finished_spans()).SerializeToString()


TRACED = ("sdk", "deployed", "chat", "tagging", "worker", "bulk")


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        for name in TRACED:
            await seed_agent(
                session_factory,
                name,
                "admin",
                harness_url=None,
                trajectory_source=TrajectorySource.OTEL,
                mapping=reference_mapping(),
            )
        await seed_agent(session_factory, "pusher", "admin")
        yield client


async def export(client: httpx.AsyncClient, payload: bytes, headers: dict) -> int:
    return (await client.post("/api/otel/v1/traces", content=payload, headers=headers)).status_code


def bearer(client: httpx.AsyncClient, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=client._transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_protobuf_export_by_header_stores_spans(api):
    headers = {**PROTO, "X-Evalkit-Agent": "sdk"}
    resp = await api.post("/api/otel/v1/traces", content=otlp_payload(), headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/x-protobuf"
    agents = {a["slug"]: a for a in (await api.get("/api/agents")).json()}
    assert agents["sdk@admin"]["trajectory_source"] == "otel"
    session = (await api.get("/api/agents/sdk@admin/sessions/sess-1/trajectory")).json()
    assert session["span_count"] == 2 and session["root_present"]
    assert session["settled"] is False
    spans = (await api.get("/api/agents/sdk@admin/sessions/sess-1/spans")).json()
    assert {s["name"] for s in spans} == {"evalkit.run", "llm"}
    assert all("session_id_hint" not in s for s in spans)


async def test_json_export_by_resource_attribute_and_gzip(api):
    body = {
        "resourceSpans": [
            {
                "resource": {
                    "attributes": [{"key": "evalkit.agent", "value": {"stringValue": "sdk"}}]
                },
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "0af7651916cd43dd8448eb211c80319c",
                                "spanId": "b7ad6b7169203331",
                                "name": "evalkit.run",
                                "kind": 1,
                                "startTimeUnixNano": "1700000000000000000",
                                "endTimeUnixNano": "1700000001000000000",
                                "attributes": [
                                    {"key": "session.id", "value": {"stringValue": "j-1"}},
                                    {"key": "n", "value": {"intValue": "3"}},
                                    {
                                        "key": "tags",
                                        "value": {"arrayValue": {"values": [{"stringValue": "a"}]}},
                                    },
                                ],
                                "status": {"code": 2, "message": "boom"},
                            }
                        ]
                    }
                ],
            }
        ]
    }
    resp = await api.post(
        "/api/otel/v1/traces",
        content=gzip.compress(json.dumps(body).encode()),
        headers={"content-type": "application/json", "content-encoding": "gzip"},
    )
    assert resp.status_code == 200, resp.text
    spans = (await api.get("/api/agents/sdk@admin/sessions/j-1/spans")).json()
    assert spans[0]["attributes"] == {"session.id": "j-1", "n": 3, "tags": ["a"]}
    assert spans[0]["status_code"] == "ERROR" and spans[0]["status_message"] == "boom"
    assert spans[0]["trace_id"] == "0af7651916cd43dd8448eb211c80319c"


async def test_foreign_workspace_unknown_agent_and_size_cap(api):
    alice = await login_as(api, "alice", "password")
    payload = otlp_payload()
    foreign = {**PROTO, "X-Evalkit-Agent": "sdk@admin"}
    assert (
        await alice.post("/api/otel/v1/traces", content=payload, headers=foreign)
    ).status_code == 403
    canon = {**PROTO, "X-Evalkit-Agent": "fake@canonical"}
    assert (
        await alice.post("/api/otel/v1/traces", content=payload, headers=canon)
    ).status_code == 403
    assert (
        await alice.post("/api/otel/v1/traces", content=payload, headers=PROTO)
    ).status_code == 400
    api.app.state.cfg.otel.max_batch_bytes = 10
    own = {**PROTO, "X-Evalkit-Agent": "sdk"}
    assert (await api.post("/api/otel/v1/traces", content=payload, headers=own)).status_code == 413
    await alice.aclose()


async def test_agent_token_implies_agent_and_rejects_another(api):
    token = (await api.post("/api/tokens", json={"agent": "deployed"})).json()["token"]
    client = bearer(api, token)
    ok = await client.post(
        "/api/otel/v1/traces", content=otlp_payload(session_id="d-1"), headers=PROTO
    )
    assert ok.status_code == 200, ok.text
    other = {**PROTO, "X-Evalkit-Agent": "other"}
    assert (
        await client.post("/api/otel/v1/traces", content=otlp_payload(), headers=other)
    ).status_code == 403
    session = (await api.get("/api/agents/deployed@admin/sessions/d-1/trajectory")).json()
    assert session["span_count"] == 2
    await client.aclose()


async def test_resend_upserts(api):
    payload = otlp_payload(session_id="again")
    headers = {**PROTO, "X-Evalkit-Agent": "sdk"}
    for _ in range(2):
        await api.post("/api/otel/v1/traces", content=payload, headers=headers)
    session = (await api.get("/api/agents/sdk@admin/sessions/again/trajectory")).json()
    assert session["span_count"] == 2


async def test_export_never_flips_a_known_agents_trajectory_source(api):
    body = {
        "dataset": "finance@canonical",
        "agent": "pusher@admin",
        "capabilities": CAPS,
        "holder": "cli:admin@test:1",
    }
    assert (await api.post("/api/experiments", json=body)).status_code == 200
    headers = {**PROTO, "X-Evalkit-Agent": "pusher"}
    assert await export(api, otlp_payload(session_id="stray"), headers) == 200
    agents = {a["slug"]: a for a in (await api.get("/api/agents")).json()}
    assert agents["pusher@admin"]["trajectory_source"] == "atif"
    assert (
        await export(
            api, otlp_payload(session_id="c-1"), {**PROTO, "X-Evalkit-Agent": "fake@canonical"}
        )
        == 200
    )
    assert agents["fake@canonical"]["trajectory_source"] == "atif"


async def test_malformed_bodies_are_400_and_gzip_is_bounded(api):
    headers = {**PROTO, "X-Evalkit-Agent": "sdk"}
    assert await export(api, b"not a protobuf \xff\xfe", headers) == 400
    json_headers = {"content-type": "application/json", "X-Evalkit-Agent": "sdk"}
    assert await export(api, b"{not json", json_headers) == 400
    missing_ids = json.dumps(
        {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "x", "startTimeUnixNano": "1"}]}]}]}
    )
    assert await export(api, missing_ids.encode(), json_headers) == 400
    api.app.state.cfg.otel.max_batch_bytes = 64 * 1024
    bomb = gzip.compress(b"0" * (2 * 1024 * 1024))
    assert len(bomb) < 64 * 1024
    gz_headers = {**json_headers, "content-encoding": "gzip"}
    assert await export(api, bomb, gz_headers) == 413


async def test_streamed_batch_stops_at_max_batch_bytes(api):
    api.app.state.cfg.otel.max_batch_bytes = 10

    async def chunks():
        for _ in range(100):
            yield b"0123456789"

    headers = {**PROTO, "X-Evalkit-Agent": "sdk"}
    resp = await api.post("/api/otel/v1/traces", content=chunks(), headers=headers)
    assert "content-length" not in resp.request.headers
    assert resp.status_code == 413
    assert resp.json()["detail"] == (
        "trace batch is larger than the 10 bytes limit (server config otel.max_batch_bytes)"
    )


def two_agent_payload(resource_agent=None) -> bytes:
    exporter = InMemorySpanExporter()
    resource = Resource.create({"evalkit.agent": resource_agent} if resource_agent else {})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("t")
    for agent, session in (("chat", "c-1"), ("tagging", "t-1"), (None, "h-1")):
        attributes = {"session.id": session, "openinference.span.kind": "CHAIN"}
        if agent:
            attributes["evalkit.agent"] = agent
        with tracer.start_as_current_span("turn", attributes=attributes):
            pass
    return encode_spans(exporter.get_finished_spans()).SerializeToString()


async def span_count(client: httpx.AsyncClient, agent: str, session: str) -> int:
    resp = await client.get(f"/api/agents/{agent}/sessions/{session}/trajectory")
    return resp.json()["span_count"] if resp.status_code == 200 else 0


async def test_span_attribute_picks_the_agent_and_the_header_is_the_default(api):
    headers = {**PROTO, "X-Evalkit-Agent": "worker"}
    assert await export(api, two_agent_payload(), headers) == 200
    assert await span_count(api, "chat@admin", "c-1") == 1
    assert await span_count(api, "tagging@admin", "t-1") == 1
    assert await span_count(api, "worker@admin", "h-1") == 1
    assert await span_count(api, "worker@admin", "c-1") == 0
    assert await export(api, two_agent_payload(resource_agent="bulk"), headers) == 200
    assert await span_count(api, "bulk@admin", "h-1") == 1
    assert await span_count(api, "chat@admin", "c-1") == 2


async def test_every_named_agent_is_authorized_before_anything_is_stored(api):
    alice = await login_as(api, "alice", "password")
    assert await export(alice, two_agent_payload(), PROTO) == 400
    foreign = two_agent_payload(resource_agent="sdk@admin")
    assert await export(alice, foreign, {**PROTO, "X-Evalkit-Agent": "mine"}) == 404
    assert await export(alice, otlp_payload(agent_attr="sdk@admin"), PROTO) == 403
    assert await span_count(api, "sdk@admin", "sess-1") == 0
    token = (await api.post("/api/tokens", json={"agent": "chat"})).json()["token"]
    bound = bearer(api, token)
    assert await export(bound, two_agent_payload(), PROTO) == 403
    assert await span_count(api, "chat@admin", "c-1") == 0
    await alice.aclose()
    await bound.aclose()


async def test_rejections_are_logged_with_agent_reason_and_status(api, caplog):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from evalkit.db.models import AuthToken

    caplog.set_level("WARNING", logger="evalkit.api.otel")
    alice = await login_as(api, "alice", "password")
    headers = {**PROTO, "X-Evalkit-Agent": "sdk@admin"}
    assert await export(alice, otlp_payload(), headers) == 403
    assert await export(api, b"\xff\xfe", {**PROTO, "X-Evalkit-Agent": "sdk"}) == 400
    assert await export(api, otlp_payload(), {**PROTO, "X-Evalkit-Agent": "nope@canonical"}) == 404
    api.app.state.cfg.otel.max_batch_bytes = 10
    assert await export(api, otlp_payload(), {**PROTO, "X-Evalkit-Agent": "sdk"}) == 413
    async with api.app.state.session_factory() as session:
        await session.execute(
            update(AuthToken).values(expires_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await session.commit()
    assert await export(alice, otlp_payload(), {**PROTO, "X-Evalkit-Agent": "late"}) == 401
    logged = [
        (r.otel_rejection["status"], r.otel_rejection["reason"], r.otel_rejection["agent"])
        for r in caplog.records
        if hasattr(r, "otel_rejection")
    ]
    assert logged == [
        (403, "forbidden", "sdk@admin"),
        (400, "malformed", "sdk"),
        (404, "unknown agent", "nope@canonical"),
        (413, "too large", "sdk"),
        (401, "expired token", "late"),
    ]
    await alice.aclose()


async def test_archived_workspace_refuses_trace_writes(api):
    await api.post("/api/workspaces/admin/archive")
    assert await export(api, otlp_payload(), {**PROTO, "X-Evalkit-Agent": "sdk"}) == 409
    minted = await api.post("/api/tokens", json={"agent": "sdk"})
    assert minted.status_code == 409 and "archived" in minted.json()["detail"]
    draft = await api.put("/api/agents/sdk@admin/mappings/draft", json=reference_mapping())
    assert draft.status_code == 409


async def shared_agent_token(api, session_factory) -> httpx.AsyncClient:
    await seed_agent(
        session_factory,
        "team",
        "canonical",
        harness_url=None,
        trajectory_source=TrajectorySource.OTEL,
        mapping=reference_mapping(),
    )
    alice = await login_as(api, "alice", "password")
    await WorkspaceStore(session_factory).add_member(
        "canonical", "alice", WorkspaceRole.WRITER, granted_by=1
    )
    minted = await alice.post("/api/tokens", json={"agent": "team@canonical"})
    assert minted.status_code == 200, minted.text
    await alice.aclose()
    token = bearer(api, minted.json()["token"])
    assert await export(token, otlp_payload(session_id="t-1"), PROTO) == 200
    return token


async def test_agent_token_outlives_its_creator_losing_access(api, session_factory):
    token = await shared_agent_token(api, session_factory)
    removed = await api.delete("/api/workspaces/canonical/members/alice/writer")
    assert removed.status_code == 200, removed.text
    assert (await api.post("/api/users/alice/archive")).status_code == 200
    assert await export(token, otlp_payload(session_id="t-2"), PROTO) == 200
    await token.aclose()


async def test_archiving_the_agent_deletes_its_tokens(api, session_factory):
    minted = (await api.post("/api/tokens", json={"agent": "deployed"})).json()
    token = bearer(api, minted["token"])
    assert await export(token, otlp_payload(session_id="a-1"), PROTO) == 200
    assert (await api.post("/api/agents/deployed@admin/archive")).status_code == 200
    assert await export(token, otlp_payload(session_id="a-2"), PROTO) == 401
    async with session_factory() as session:
        left = await session.scalar(select(AuthToken).where(AuthToken.id == minted["id"]))
    assert left is None
    await token.aclose()


async def test_agent_token_stops_when_the_workspace_is_archived(api):
    minted = (await api.post("/api/tokens", json={"agent": "deployed"})).json()
    token = bearer(api, minted["token"])
    assert (await api.post("/api/workspaces/admin/archive")).status_code == 200
    assert await export(token, otlp_payload(session_id="w-1"), PROTO) == 409
    await token.aclose()


async def test_login_token_cannot_export_to_an_archived_agent(api):
    assert (await api.post("/api/agents/deployed@admin/archive")).status_code == 200
    headers = {**PROTO, "X-Evalkit-Agent": "deployed"}
    assert await export(api, otlp_payload(session_id="p-1"), headers) == 409
