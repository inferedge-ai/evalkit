import asyncio
import time
from datetime import UTC, datetime

import httpx2
import pytest
from fastapi import FastAPI, HTTPException, Query
from opentelemetry import trace
from opentelemetry.context import Context
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult

from evalkit.api.app import create_app
from evalkit.config import OtelConfig
from evalkit.contracts.enums import TraceProvider, TrajectorySource
from evalkit.contracts.settings import AgentSettings
from evalkit.db.session import make_session_factory
from evalkit.db.trace_sources import TraceSourceStore
from evalkit.sdk import RunResult, aevaluate_experiment, arun_experiment
from evalkit.sdk.runner import NO_SESSION_ERROR
from tests.cli.test_cli import _serve, stub_judges_module
from tests.fake_agents import reference_mapping
from tests.fake_harness import FakeHarness
from tests.identity import TEST_BOX, seed_agent
from tests.native import HOLDER, seed_env
from tests.sdk.test_run_experiment import LLM_ATTRS, Server

PROJECT = "ra"
TRACE_IDS = Query()
AGENT = "imported"

__all__ = ["stub_judges_module"]


def _iso(nanos: int) -> str:
    return datetime.fromtimestamp(nanos / 1e9, UTC).isoformat()


class FakePhoenix(SpanExporter):
    def __init__(self) -> None:
        self.spans: list[dict] = []
        self.app = FastAPI()
        self.app.get("/v1/projects/{project}")(self.project)
        self.app.get("/v1/projects/{project}/traces")(self.traces)
        self.app.get("/v1/projects/{project}/spans")(self.spans_of)

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            parent = span.parent
            self.spans.append(
                {
                    "name": span.name,
                    "context": {
                        "trace_id": f"{span.context.trace_id:032x}",
                        "span_id": f"{span.context.span_id:016x}",
                    },
                    "parent_id": f"{parent.span_id:016x}" if parent else None,
                    "span_kind": "INTERNAL",
                    "start_time": _iso(span.start_time),
                    "end_time": _iso(span.end_time),
                    "status_code": "OK",
                    "attributes": dict(span.attributes or {}),
                    "events": [],
                }
            )
        return SpanExportResult.SUCCESS

    def add_harness_run(self, session_id: str) -> None:
        n = len(self.spans) + 1
        self.spans.append(
            {
                "name": "harness turn",
                "context": {"trace_id": f"{n:032x}", "span_id": f"{n:016x}"},
                "parent_id": None,
                "span_kind": "INTERNAL",
                "start_time": "2026-11-13T00:00:00+00:00",
                "end_time": "2026-11-13T00:00:01+00:00",
                "status_code": "OK",
                "attributes": {**LLM_ATTRS, "session.id": session_id},
                "events": [],
            }
        )

    def shutdown(self) -> None:
        return None

    def project(self, project: str) -> dict:
        if project != PROJECT:
            raise HTTPException(status_code=404)
        return {"data": {"name": project}}

    def traces(self, project: str, session_identifier: str) -> dict:
        if session_identifier.startswith("sess-") and not any(
            s["attributes"].get("session.id") == session_identifier for s in self.spans
        ):
            self.add_harness_run(session_identifier)
        roots = [
            s
            for s in self.spans
            if s["parent_id"] is None and s["attributes"].get("session.id") == session_identifier
        ]
        data = [
            {"trace_id": s["context"]["trace_id"], "start_time": s["start_time"]} for s in roots
        ]
        return {"data": data, "next_cursor": None}

    def spans_of(self, project: str, trace_id: list[str] = TRACE_IDS) -> dict:
        data = [s for s in self.spans if s["context"]["trace_id"] in trace_id]
        return {"data": data, "next_cursor": None}


@pytest.fixture(scope="module")
def phoenix():
    fake = FakePhoenix()
    uv_server, thread, port = _serve(fake.app)
    yield fake, f"http://127.0.0.1:{port}"
    uv_server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def server(tmp_path_factory, stub_judges_module, module_db_url, phoenix):
    _, phoenix_url = phoenix
    tmp_path = tmp_path_factory.mktemp("import")

    async def setup():
        factory = make_session_factory(module_db_url)
        env = await seed_env(factory, tmp_path, FakeHarness())
        await TraceSourceStore(factory, "default", TEST_BOX).create(
            "phx", TraceProvider.PHOENIX, phoenix_url, None
        )
        await seed_agent(
            factory,
            AGENT,
            "admin",
            harness_url=None,
            trajectory_source=TrajectorySource.OTEL,
            settings=AgentSettings(
                trace_source="phx", project=PROJECT, import_settle_seconds=0, import_wait_seconds=3
            ),
            mapping=reference_mapping(),
        )
        await seed_agent(
            factory,
            "harnessed",
            "canonical",
            trajectory_source=TrajectorySource.OTEL,
            settings=AgentSettings(
                trace_source="phx", project=PROJECT, import_settle_seconds=0, import_wait_seconds=3
            ),
            mapping=reference_mapping(),
        )
        await factory.kw["bind"].dispose()
        return factory, env

    factory, env = asyncio.run(setup())
    env.deps.cfg = env.deps.cfg.model_copy(update={"otel": OtelConfig(settle_seconds=0)})
    app = create_app(
        env.deps.cfg,
        blob_store=env.blob,
        runner_deps=env.deps,
        session_factory=factory,
        check_schema=False,
        holder=HOLDER,
    )
    app.state.box = TEST_BOX
    uv_server, thread, port = _serve(app)
    yield f"http://127.0.0.1:{port}", app, env.blob
    uv_server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def env(server, phoenix, tmp_path, monkeypatch, module_db_url):
    base_url, app, blob = server
    fake, _ = phoenix
    login = httpx2.post(
        f"{base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    )
    monkeypatch.setenv("EVALKIT_SERVER_URL", base_url)
    monkeypatch.setenv("EVALKIT_TOKEN", login.json()["token"])
    cost_map = tmp_path / "cost_map.json"
    cost_map.write_text("{}")
    (tmp_path / "evalkit" / "fixtures").mkdir(parents=True)
    (tmp_path / "evalkit.yaml").write_text(
        f"cases: evalkit/cases\nfixtures: evalkit/fixtures\ncost_map:\n  source: {cost_map}\n"
    )
    monkeypatch.chdir(tmp_path)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(fake))
    return Server(base_url, app, blob, module_db_url), provider


async def run(provider, task, **kwargs):
    lines: list[str] = []
    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent=AGENT,
        task=task,
        tracer_provider=provider,
        poll_interval=0.2,
        progress=lines.append,
        **kwargs,
    )
    return result, lines


async def test_flow_18_an_unchanged_task_with_an_in_process_agent(env):
    srv, provider = env
    tracer = provider.get_tracer("agent")

    def task(input: dict) -> str:
        with tracer.start_as_current_span("llm", attributes=LLM_ATTRS):
            pass
        return "42"

    result, _ = await run(provider, task)
    assert [r.trajectory_status for r in result.runs] == ["valid", "valid"]
    assert all(r.trajectory_note is None for r in result.runs)
    _, runs = await srv.experiment(result.experiment_id)
    assert {r.agent_run.trajectory_status for r in runs} == {"valid"}


async def test_flow_18_an_agent_with_its_own_trace_takes_the_session_id(env):
    _, provider = env
    tracer = provider.get_tracer("agent")

    def task(input: dict, metadata: dict) -> str:
        attributes = {**LLM_ATTRS, "session.id": metadata["session_id"]}
        with tracer.start_as_current_span("llm", context=Context(), attributes=attributes):
            pass
        return "42"

    result, _ = await run(provider, task)
    assert [r.trajectory_status for r in result.runs] == ["valid", "valid"]


async def test_flow_18_only_the_run_span_is_unavailable_with_a_note(env):
    srv, provider = env
    tracer = provider.get_tracer("agent")

    def task(input: dict) -> str:
        with tracer.start_as_current_span("llm", context=Context(), attributes=LLM_ATTRS):
            pass
        return "42"

    result, lines = await run(provider, task)
    assert [r.trajectory_status for r in result.runs] == ["unavailable", "unavailable"]
    note = result.runs[0].trajectory_note
    assert note.startswith("only evalkit's run span was found")
    _, runs = await srv.experiment(result.experiment_id)
    assert {r.agent_run.trajectory_note for r in runs} == {note}
    assert any(line.strip() == note for line in lines)


async def test_flow_18_dry_run_names_the_trace_source(env):
    _, provider = env
    tracer = provider.get_tracer("agent")

    def task(input: dict) -> str:
        with tracer.start_as_current_span("llm", attributes=LLM_ATTRS):
            pass
        return "42"

    result, lines = await run(provider, task, dry_run=True)
    assert lines[0] == f"trajectories: imported from phx, project {PROJECT}"
    assert [r.trajectory_status for r in result.runs] == ["valid"]


async def test_flow_18_no_run_span_and_no_way_to_name_the_session_fails_at_once(env):
    def task(input: dict) -> str:
        if input["question"] == "q2":
            raise RuntimeError("task blew up")
        return "42"

    result, _ = await run(trace.NoOpTracerProvider(), task)
    by_case = {r.case_id: r for r in result.runs}
    assert by_case["case-1"].status == "failed" and by_case["case-1"].session_id is None
    srv, _ = env
    _, runs = await srv.experiment(result.experiment_id)
    errors = {r.case_id: r.agent_run.error for r in runs}
    assert errors["case-1"] == NO_SESSION_ERROR
    assert "task blew up" in errors["case-2"]


async def test_flow_18_a_task_that_returns_its_own_session(env):
    _, provider = env
    tracer = provider.get_tracer("agent")
    counter = iter(range(100))

    def task(input: dict) -> RunResult:
        session_id = f"own-{next(counter)}"
        attributes = {**LLM_ATTRS, "session.id": session_id}
        with tracer.start_as_current_span("llm", context=Context(), attributes=attributes):
            pass
        return RunResult(answer="42", session_id=session_id)

    result, _ = await run(provider, task)
    assert [r.trajectory_status for r in result.runs] == ["valid", "valid"]
    assert {r.session_id for r in result.runs} == {"own-0", "own-1"}


async def test_flow_18_a_refetch_finds_late_spans_and_clears_the_note(env, phoenix):
    srv, provider = env
    fake, _ = phoenix
    tracer = provider.get_tracer("agent")

    def task(input: dict) -> str:
        with tracer.start_as_current_span("llm", context=Context(), attributes=LLM_ATTRS):
            pass
        return "42"

    result, _ = await run(provider, task)
    assert {r.trajectory_status for r in result.runs} == {"unavailable"}
    for summary in result.runs:
        fake.add_harness_run(summary.session_id)
    again = await aevaluate_experiment(
        experiment_id=result.experiment_id, refetch_trajectories=True, progress=lambda line: None
    )
    assert [r.trajectory_status for r in again.runs] == ["valid", "valid"]
    token = httpx2.post(
        f"{srv.base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    ).json()["token"]
    runs = httpx2.get(
        f"{srv.base_url}/api/experiments/{result.experiment_id}/runs",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert {
        (r["agent_run"]["trajectory_status"], r["agent_run"]["trajectory_note"]) for r in runs
    } == {("valid", None)}


def test_flow_18_a_server_run_of_a_harness_agent_pulls_from_the_store(env):
    srv, _ = env
    token = httpx2.post(
        f"{srv.base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    ).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    started = httpx2.post(
        f"{srv.base_url}/api/experiments",
        json={"dataset": "finance@canonical", "agent": "harnessed@canonical"},
        headers=headers,
    )
    assert started.status_code == 200, started.text
    exp_id = started.json()["id"]
    for _ in range(200):
        body = httpx2.get(f"{srv.base_url}/api/experiments/{exp_id}", headers=headers).json()
        if body["status"] in ("completed", "failed") and body["lease_holder"] is None:
            break
        time.sleep(0.05)
    assert body["status"] == "completed", body
    runs = httpx2.get(f"{srv.base_url}/api/experiments/{exp_id}/runs", headers=headers).json()
    assert {r["agent_run"]["trajectory_status"] for r in runs} == {"valid"}
