import asyncio
import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from evalkit.core.traces.presets import read_preset
from evalkit.db.crypto import SecretBox
from tests.flows.helpers_12_17 import DEV, flow_api

pytestmark = pytest.mark.e2e

KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
PROJECT = os.environ.get("LANGFUSE_PROJECT", "e2e-test")
AGENT = f"live@{DEV}"


def seed_otel_trace(service: str, session: str) -> None:
    keys = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
    exporter = OTLPSpanExporter(
        endpoint=f"{os.environ['LANGFUSE_BASE_URL']}/api/public/otel/v1/traces",
        headers={
            "Authorization": f"Basic {base64.b64encode(keys.encode()).decode()}",
            "x-langfuse-ingestion-version": "4",
        },
    )
    provider = TracerProvider(resource=Resource.create({"service.name": service}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(service)
    root = {"gen_ai.operation.name": "invoke_agent", "session.id": session}
    with tracer.start_as_current_span(f"invoke_agent {service}", attributes=root):
        with tracer.start_as_current_span(
            "chat claude-sonnet-4-5",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "claude-sonnet-4-5",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.output_tokens": 5,
                "gen_ai.input.messages": json.dumps(
                    [{"role": "user", "parts": [{"type": "text", "content": "hi"}]}]
                ),
                "gen_ai.output.messages": json.dumps(
                    [{"role": "assistant", "parts": [{"type": "text", "content": "hello"}]}]
                ),
            },
        ):
            pass
    provider.shutdown()


@pytest.fixture
async def api(tmp_path, session_factory, stub_judges):
    if not all(os.environ.get(k) for k in KEYS):
        pytest.skip(f"set {', '.join(KEYS)} for the live Langfuse test")
    async with flow_api(tmp_path, session_factory) as (client, _env):
        client.app.state.box = SecretBox(Fernet.generate_key().decode())
        client.app.state.cfg.allow_private_endpoints = True
        keys = f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}"
        source = {
            "name": "lf",
            "provider": "langfuse",
            "url": os.environ["LANGFUSE_BASE_URL"],
            "api_key": keys,
        }
        assert (await client.post("/api/trace-sources", json=source)).status_code == 200
        yield client


async def settled(api, import_id: int) -> dict:
    for _ in range(120):
        view = (await api.get(f"/api/agents/{AGENT}/imports/{import_id}")).json()
        if view["status"] not in ("running", "stopping"):
            return view
        await asyncio.sleep(0.5)
    raise AssertionError(f"import {import_id} still {view['status']}")


async def test_live_langfuse_session_and_range_import(api):
    service = f"evalkit-e2e-{uuid.uuid4().hex[:8]}"
    session = f"e2e-{uuid.uuid4().hex[:8]}"
    started_at = datetime.now(UTC) - timedelta(minutes=1)
    seed_otel_trace(service, session)
    seed_otel_trace(f"{service}-other", f"{session}-other")
    trace_filter = json.dumps(
        [
            {
                "type": "stringObject",
                "column": "metadata",
                "key": "resourceAttributes.service.name",
                "operator": "=",
                "value": service,
            }
        ]
    )
    created = await api.post(
        "/api/agents",
        json={
            "workspace": DEV,
            "name": "live",
            "trajectory_source": "otel",
            "mapping": json.loads(read_preset("langfuse")),
            "settings": {"trace_source": "lf", "project": PROJECT, "trace_filter": trace_filter},
        },
    )
    assert created.status_code == 200, created.text
    imported = 0
    for _ in range(40):
        reply = await api.post(f"/api/agents/{AGENT}/sessions/import", json={"session_id": session})
        assert reply.status_code == 200, reply.text
        imported = reply.json()["imported"]
        if imported >= 2:
            break
        await asyncio.sleep(3)
    assert imported == 2
    trajectory = (await api.get(f"/api/agents/{AGENT}/sessions/{session}/trajectory")).json()
    steps = trajectory["trajectory"]["steps"]
    assert [s["source"] for s in steps] == ["user", "agent"]
    assert steps[-1]["message"] == "hello"
    assert steps[-1]["metrics"]["prompt_tokens"] == 100
    window = {
        "since": started_at.isoformat(),
        "until": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
    }
    started = await api.post(f"/api/agents/{AGENT}/imports", json=window)
    assert started.status_code == 202, started.text
    view = await settled(api, started.json()["id"])
    assert view["status"] == "done", view
    assert view["traces"] == 1 and view["progress"] == 1.0
