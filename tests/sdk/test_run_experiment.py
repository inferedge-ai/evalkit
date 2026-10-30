import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor

from evalkit.api.app import create_app
from evalkit.client import connection
from evalkit.client.api import ApiRequestError
from evalkit.config import OtelConfig
from evalkit.contracts.enums import EvaluatorKind, TrajectorySource
from evalkit.contracts.settings import AgentSettings, RemoteEvaluatorSettings
from evalkit.core.runner import ScoringFailed
from evalkit.credentials import MissingCredential
from evalkit.db import models
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.session import make_session_factory
from evalkit.db.store import PgStore
from evalkit.sdk import (
    RunResult,
    aevaluate_experiment,
    arun_experiment,
    evaluator,
    run_experiment,
    tracing,
)
from evalkit.sdk import runner as sdk_runner
from evalkit.sdk.runner import _local_config
from evalkit.store.base import Experiment, RunDetail
from tests.cli.test_cli import _serve
from tests.fake_agents import reference_mapping
from tests.fake_harness import DEFAULT_TRAJECTORY, FakeHarness
from tests.identity import seed_agent
from tests.native import HOLDER, seed_env

LLM_ATTRS = {
    "openinference.span.kind": "LLM",
    "llm.model_name": "m",
    "llm.output_messages.0.message.role": "assistant",
    "llm.output_messages.0.message.content": "42",
    "llm.token_count.prompt": 3,
    "llm.token_count.completion": 1,
}


@pytest.fixture(scope="module")
def server(tmp_path_factory, stub_judges_module, module_db_url):
    tmp_path = tmp_path_factory.mktemp("sdk")

    async def setup():
        factory = make_session_factory(module_db_url)
        env = await seed_env(factory, tmp_path, FakeHarness())
        await seed_agent(
            factory,
            "sdk",
            "admin",
            harness_url=None,
            trajectory_source=TrajectorySource.OTEL,
            settings=AgentSettings(trajectory_wait_seconds=5),
            mapping=reference_mapping(),
        )
        await seed_agent(factory, "pushed", "admin", harness_url=None, settings=AgentSettings())
        await seed_agent(factory, "harnessy", "admin", harness_url="http://x")
        await RemoteEvaluatorStore(factory).create(
            "admin",
            "http_eval",
            EvaluatorKind.SCORE,
            RemoteEvaluatorSettings(endpoint="http://custom.test/rtc"),
        )
        await factory.kw["bind"].dispose()
        return factory, env

    factory, env = asyncio.run(setup())
    cfg = env.deps.cfg.model_copy(update={"otel": OtelConfig(settle_seconds=0)})
    env.deps.cfg = cfg
    app = create_app(
        cfg,
        blob_store=env.blob,
        runner_deps=env.deps,
        session_factory=factory,
        check_schema=False,
        holder=HOLDER,
    )
    uv_server, thread, port = _serve(app)
    yield f"http://127.0.0.1:{port}", app, env.blob
    uv_server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def env(server, tmp_path, monkeypatch, module_db_url):
    base_url, app, blob = server
    login = httpx.post(
        f"{base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    )
    token = login.json()["token"]
    monkeypatch.setenv("EVALKIT_SERVER_URL", base_url)
    monkeypatch.setenv("EVALKIT_TOKEN", token)
    cost_map = tmp_path / "cost_map.json"
    cost_map.write_text("{}")
    (tmp_path / "eval" / "fixtures").mkdir(parents=True)
    (tmp_path / "evalkit.yaml").write_text(
        f"cases: eval/cases\nfixtures: eval/fixtures\ncost_map:\n  source: {cost_map}\n"
        "credentials:\n  evaluators:\n    http_eval: env:SDK_EVAL_KEY\n"
        "  llm_providers:\n    http://judge.test: env:SDK_JUDGE_KEY\n"
    )
    (tmp_path / ".env").write_text("SDK_EVAL_KEY=k\nSDK_JUDGE_KEY=jk\n")
    monkeypatch.chdir(tmp_path)
    provider = TracerProvider()
    headers = {"Authorization": f"Bearer {token}", "X-Evalkit-Agent": "sdk"}
    exporter = OTLPSpanExporter(endpoint=tracing.collector_url(base_url), headers=headers)
    tracing.configure(provider, exporter)
    return Server(base_url, app, blob, module_db_url), blob, provider


class Server:
    def __init__(self, base_url: str, app, blob, db_url: str):
        self.base_url = base_url
        self.app = app
        self.blob = blob
        self.db_url = db_url

    async def _with_store(self, work):
        factory = make_session_factory(self.db_url)
        result = await work(PgStore(factory), factory)
        await factory.kw["bind"].dispose()
        return result

    async def experiment(self, exp_id: int) -> tuple[Experiment, list[RunDetail]]:
        async def work(store, _):
            return await store.get_experiment(exp_id), await store.run_details(exp_id)

        return await self._with_store(work)

    async def count(self) -> int:
        async def work(_, factory):
            async with factory() as session:
                return await session.scalar(sa.select(sa.func.count(models.Experiment.id)))

        return await self._with_store(work)

    async def execute(self, statement) -> None:
        async def work(_, factory):
            async with factory() as session:
                await session.execute(statement)
                await session.commit()

        await self._with_store(work)


async def test_sdk_run_with_traces_records_runs_after_settle(env):
    srv, blob, provider = env
    tracer = provider.get_tracer("client")
    seen: list[dict] = []

    def task(input: dict, metadata: dict) -> str:
        seen.append(metadata)
        with tracer.start_as_current_span("llm", attributes=LLM_ATTRS):
            pass
        return "42"

    @evaluator(name="required_tool_calls")
    def cite(case, run):
        return 1.0 if run.trajectory else 0.0

    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=task,
        evaluators=[cite],
        tracer_provider=provider,
        poll_interval=0.2,
    )
    assert isinstance(result.experiment_id, int)
    assert result.url.endswith(f"/experiments/{result.experiment_id}")
    assert sorted(seen[0]) == ["case_id", "experiment_id", "repetition", "session_id", "tags"]
    assert seen[0]["experiment_id"] == result.experiment_id
    assert {m["session_id"] for m in seen} == {r.session_id for r in result.runs}
    exp, runs = await srv.experiment(result.experiment_id)
    assert exp.status == "completed" and exp.config.snapshot.trajectory_source == "otel"
    assert exp.config.snapshot.code_evaluators == ["required_tool_calls"]
    assert exp.config.snapshot.capabilities["contract_version"] == "sdk"
    assert exp.lease_holder is None
    for run in runs:
        record = run.agent_run
        assert record.status == "completed" and record.trajectory_status == "valid"
        assert record.answer == "42"
        doc = json.loads(blob.objects[record.trajectory_key])
        assert doc["extra"]["trajectory_source"] == "otel"
        assert doc["steps"][-1]["message"] == "42"
        assert record.metrics["input_tokens"] == 3
        names = {e.name: e for e in run.evaluations}
        assert names["required_tool_calls"].value == 1.0
        assert names["required_tool_calls"].source == "code"
    assert {r.trajectory_status for r in result.runs} == {"valid"}


def remote_agent(base_url: str) -> TracerProvider:
    token = httpx.post(
        f"{base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    ).json()["token"]
    provider = TracerProvider()
    headers = {"Authorization": f"Bearer {token}", "X-Evalkit-Agent": "sdk"}
    exporter = OTLPSpanExporter(endpoint=tracing.collector_url(base_url), headers=headers)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def runs_page(base_url: str, query: str) -> list[dict]:
    token = httpx.post(
        f"{base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    ).json()["token"]
    resp = httpx.get(
        f"{base_url}/api/agents/sdk@admin/runs",
        params={"query": query},
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.json()["runs"]


async def test_continued_trace_with_its_own_session_lists_once(env):
    srv, blob, provider = env
    base_url = srv.base_url
    remote = remote_agent(base_url).get_tracer("research-agent")
    seen: list[dict] = []

    def task(input: dict, metadata: dict) -> str:
        seen.append(metadata)
        carrier: dict[str, str] = {}
        propagate.inject(carrier)
        own = {**LLM_ATTRS, "gen_ai.conversation.id": f"own-{metadata['case_id']}"}
        parent = propagate.extract(carrier)
        with remote.start_as_current_span("llm", context=parent, attributes=own):
            pass
        return "42"

    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=task,
        tracer_provider=provider,
        case_ids=["case-1"],
        poll_interval=0.2,
    )
    run = result.runs[0]
    assert run.trajectory_status == "valid" and run.session_id == seen[0]["session_id"]
    assert runs_page(base_url, "own-case-1") == []
    [listed] = runs_page(base_url, run.session_id)
    assert listed["case_id"] == "case-1" and listed["experiment_id"] == result.experiment_id
    assert listed["title"] == "q1"


async def test_returned_session_absorbs_the_sdk_root_and_links_the_case(env):
    srv, blob, provider = env
    base_url = srv.base_url
    remote = remote_agent(base_url).get_tracer("support-bot")

    def task(input: dict, metadata: dict) -> RunResult:
        session = f"app-{metadata['session_id']}"
        turn = {
            "openinference.span.kind": "CHAIN",
            "session.id": session,
            "output.value": "42",
        }
        with remote.start_as_current_span("chat_turn", context=Context(), attributes=turn):
            with remote.start_as_current_span("llm", attributes=LLM_ATTRS):
                pass
        return RunResult(answer="42", session_id=session)

    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=task,
        tracer_provider=provider,
        case_ids=["case-1"],
        poll_interval=0.2,
    )
    run = result.runs[0]
    assert run.session_id.startswith("app-") and run.trajectory_status == "valid"
    _, [stored] = await srv.experiment(result.experiment_id)
    doc = json.loads(blob.objects[stored.agent_run.trajectory_key])
    assert doc["steps"][-1]["message"] == "42"
    token = httpx.post(
        f"{base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    ).json()["token"]
    auth = {"Authorization": f"Bearer {token}"}
    session_url = f"{base_url}/api/agents/sdk@admin/sessions/{run.session_id}"
    raw = httpx.get(f"{session_url}/spans", headers=auth).json()
    assert sorted(s["name"] for s in raw) == ["chat_turn", "evalkit.run", "llm"]
    used = httpx.get(f"{session_url}/trajectory", headers=auth).json()
    assert used["span_count"] == 2 and used["root_present"]
    assert runs_page(base_url, run.session_id.removeprefix("app-")) == []
    [listed] = runs_page(base_url, run.session_id)
    assert listed["case_id"] == "case-1" and listed["experiment_id"] == result.experiment_id


async def test_agent_in_its_own_trace_under_the_sdk_session_id(env):
    srv, blob, provider = env
    base_url = srv.base_url
    remote = remote_agent(base_url).get_tracer("worker")

    def task(input: dict, metadata: dict) -> RunResult:
        turn = {
            "openinference.span.kind": "CHAIN",
            "session.id": metadata["session_id"],
            "output.value": "42",
        }
        with remote.start_as_current_span("chat_turn", context=Context(), attributes=turn):
            with remote.start_as_current_span("llm", attributes=LLM_ATTRS):
                pass
        return RunResult(answer="42", session_id=metadata["session_id"])

    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=task,
        tracer_provider=provider,
        case_ids=["case-1"],
        poll_interval=0.2,
    )
    run = result.runs[0]
    assert run.trajectory_status == "valid"
    token = httpx.post(
        f"{base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    ).json()["token"]
    used = httpx.get(
        f"{base_url}/api/agents/sdk@admin/sessions/{run.session_id}/trajectory",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert used["span_count"] == 2
    assert [listed["case_id"] for listed in runs_page(base_url, run.session_id)] == ["case-1"]


async def test_pushed_trajectory_raising_task_and_protocol_violation(env):
    srv, blob, provider = env

    def task(input: dict):
        if input["question"] == "q1":
            return RunResult(answer="pushed", trajectory=DEFAULT_TRAJECTORY)
        raise RuntimeError("agent down")

    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=task,
        tracer_provider=provider,
        trajectory_wait_seconds=0,
    )
    by_case = {r.case_id: r for r in result.runs}
    assert by_case["case-1"].status == "completed"
    assert by_case["case-1"].trajectory_status == "valid"
    assert by_case["case-2"].status == "failed"
    recorded, runs = await srv.experiment(result.experiment_id)
    assert recorded.config.snapshot.trajectory_wait_seconds == 0
    records = {run.case_id: run.agent_run for run in runs}
    assert records["case-2"].error == "RuntimeError: agent down"
    assert records["case-2"].trajectory_status == "unavailable"

    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: 42,
        tracer_provider=provider,
        case_ids=["case-1"],
        trajectory_wait_seconds=0,
    )
    _, [run] = await srv.experiment(result.experiment_id)
    record = run.agent_run
    assert record.status == "failed" and record.error.startswith("task protocol violation")


async def test_a_reused_session_id_fails_the_later_run_and_keeps_the_first_trajectory(env):
    srv, blob, provider = env
    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: RunResult(
            answer="pushed", session_id="fixed", trajectory=DEFAULT_TRAJECTORY
        ),
        tracer_provider=provider,
        case_ids=["case-1"],
        repetitions=2,
        concurrency=1,
        trajectory_wait_seconds=0,
    )
    _, runs = await srv.experiment(result.experiment_id)
    by_status = {run.agent_run.status: run.agent_run for run in runs}
    assert by_status["completed"].harness_run_id == "fixed"
    assert by_status["failed"].error.startswith("session id fixed was already used")
    assert by_status["failed"].trajectory_key is None


async def test_a_session_id_from_an_earlier_experiment_fails_only_that_run(env):
    srv, blob, provider = env

    async def run_once():
        return await arun_experiment(
            dataset="finance",
            workspace="canonical",
            agent="pushed",
            task=lambda q: RunResult(
                answer="pushed", session_id="carried-over", trajectory=DEFAULT_TRAJECTORY
            ),
            tracer_provider=provider,
            case_ids=["case-1"],
            trajectory_wait_seconds=0,
        )

    first = await run_once()
    second = await run_once()
    _, [kept] = await srv.experiment(first.experiment_id)
    _, [refused] = await srv.experiment(second.experiment_id)
    assert kept.agent_run.harness_run_id == "carried-over" and kept.agent_run.trajectory_key
    assert refused.agent_run.status == "failed"
    assert refused.agent_run.error.startswith("run or session id carried-over was already used")
    assert refused.agent_run.trajectory_key is None


async def test_no_tracer_provider_is_unavailable(env):
    srv, blob, provider = env
    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=lambda q: "x",
        tracer_provider=trace.NoOpTracerProvider(),
        case_ids=["case-1"],
    )
    _, [run] = await srv.experiment(result.experiment_id)
    record = run.agent_run
    assert record.trajectory_status == "unavailable" and "no tracer provider" in record.error


async def test_deadline_gives_partial_then_refetch_rescored(env):
    srv, blob, provider = env
    tracer = provider.get_tracer("client")

    def task(input: dict) -> str:
        attrs = {"openinference.span.kind": "LLM", "llm.token_count.prompt": 9}
        with tracer.start_as_current_span("llm", attributes=attrs):
            pass
        return "late"

    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=task,
        trajectory_wait_seconds=0,
        tracer_provider=provider,
        case_ids=["case-1"],
    )
    _, [run] = await srv.experiment(result.experiment_id)
    assert run.agent_run.trajectory_status in ("partial", "valid")
    await srv.execute(
        sa.update(models.AgentRun)
        .where(models.AgentRun.id == run.agent_run_id)
        .values(trajectory_status="unavailable")
    )
    await srv.execute(
        sa.delete(models.Evaluation).where(
            models.Evaluation.experiment_run_id == run.experiment_run_id,
            models.Evaluation.name == "gauge_input_tokens",
        )
    )
    await aevaluate_experiment(experiment_id=result.experiment_id, refetch_trajectories=True)
    exp, [run] = await srv.experiment(result.experiment_id)
    scores = {e.name: e for e in run.evaluations}
    assert scores["gauge_input_tokens"].value == 9
    assert exp.status == "completed"


async def test_dry_run_creates_nothing_and_prints(env, capsys):
    srv, blob, provider = env
    before = await srv.count()
    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: RunResult(answer="dry", trajectory=DEFAULT_TRAJECTORY),
        dry_run=True,
        tracer_provider=provider,
    )
    assert result.experiment_id is None and await srv.count() == before
    assert len(result.runs) == 1
    assert "dry" in capsys.readouterr().out


async def test_dry_run_skips_judge_key_and_a_real_run_names_it_before_any_task(env, monkeypatch):
    srv, blob, provider = env
    monkeypatch.delenv("SDK_JUDGE_KEY", raising=False)
    Path(".env").write_text("SDK_EVAL_KEY=k\n")
    called: list[str] = []

    def task(q):
        called.append(q)
        return RunResult(answer="dry", trajectory=DEFAULT_TRAJECTORY)

    common = {
        "dataset": "finance",
        "workspace": "canonical",
        "agent": "pushed",
        "task": task,
        "tracer_provider": provider,
    }
    dry = await arun_experiment(**common, dry_run=True)
    assert dry.experiment_id is None and len(dry.runs) == 1
    called.clear()
    with pytest.raises(MissingCredential, match="SDK_JUDGE_KEY"):
        await arun_experiment(**common)
    assert called == []


async def test_dry_run_applies_case_ids_and_split(env):
    srv, blob, provider = env
    common = {
        "dataset": "finance",
        "workspace": "canonical",
        "agent": "pushed",
        "task": lambda q: RunResult(answer="dry", trajectory=DEFAULT_TRAJECTORY),
        "tracer_provider": provider,
    }
    picked = await arun_experiment(**common, case_ids=["case-2"], dry_run=True)
    assert [run.case_id for run in picked.runs] == ["case-2"]
    split = await arun_experiment(**common, split="finance", dry_run=5)
    assert [run.case_id for run in split.runs] == ["case-1", "case-2"]
    empty = await arun_experiment(**common, split="no-such-split", dry_run=5)
    assert empty.runs == []


async def test_validation_errors_before_server(env):
    srv, blob, provider = env
    with pytest.raises(ValueError, match="harness_url") as refused:
        await arun_experiment(dataset="finance", agent="harnessy", task=lambda q: q)
    assert "evalkit run <dataset> --agent harnessy" in str(refused.value)
    clash = evaluator(name="http_eval")(lambda c, r: 1.0)
    with pytest.raises(ValueError, match="http_eval"):
        await arun_experiment(
            dataset="finance", agent="pushed", task=lambda q: q, evaluators=[clash]
        )
    with pytest.raises(ApiRequestError, match="create it with evalkit agents create"):
        await arun_experiment(dataset="finance", agent="ghost", task=lambda q: q)
    created = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: RunResult(answer="x", trajectory=DEFAULT_TRAJECTORY),
        case_ids=["case-1"],
        trajectory_wait_seconds=0,
    )
    exp_id = created.experiment_id
    nope = evaluator(name="nope")(lambda c, r: 1.0)
    with pytest.raises(ValueError, match="python evaluators"):
        await aevaluate_experiment(experiment_id=exp_id, evaluators=[nope])


async def test_no_local_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        sys.modules["__main__"], "__file__", str(tmp_path / "run.py"), raising=False
    )
    with pytest.raises(FileNotFoundError, match="evalkit.yaml not found"):
        await arun_experiment(dataset="finance", agent="sdk", task=lambda q: q)


def test_local_config_is_found_beside_the_script(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / "evalkit.yaml").write_text("cases: cases\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(sys.modules["__main__"], "__file__", str(project / "run.py"), raising=False)
    assert _local_config().path == project / "evalkit.yaml"
    monkeypatch.delattr(sys.modules["__main__"], "__file__", raising=False)
    with pytest.raises(FileNotFoundError, match="evalkit.yaml not found"):
        _local_config()


def test_run_experiment_refuses_a_running_loop():
    async def inner():
        with pytest.raises(RuntimeError, match="arun_experiment"):
            run_experiment(dataset="finance", agent="sdk", task=lambda q: q)

    asyncio.run(inner())


async def test_run_warns_when_the_token_expires_within_a_day(env, monkeypatch):
    srv, blob, provider = env
    real_warning = connection.token_expiry_warning
    monkeypatch.setattr(
        connection,
        "token_expiry_warning",
        lambda p, at, now: real_warning(p, at, now + timedelta(days=30)),
    )
    lines: list[str] = []
    await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: RunResult(answer="dry", trajectory=DEFAULT_TRAJECTORY),
        tracer_provider=provider,
        dry_run=True,
        progress=lines.append,
    )
    assert lines[0].startswith("warning: the token for http")


async def test_canonical_run_needs_no_cases_or_fixtures_in_evalkit_yaml(env, tmp_path):
    srv, blob, provider = env
    config = tmp_path / "evalkit.yaml"
    paths = "cases: eval/cases\nfixtures: eval/fixtures\n"
    config.write_text(config.read_text().replace(paths, ""))
    created = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: RunResult(answer="x", trajectory=DEFAULT_TRAJECTORY),
        case_ids=["case-1"],
        trajectory_wait_seconds=0,
    )
    exp, _ = await srv.experiment(created.experiment_id)
    assert exp.status == "completed"
    assert [run.status for run in created.runs] == ["completed"]


async def test_own_dataset_without_cases_runs_the_server_copy(env, tmp_path):
    srv, blob, provider = env
    config = tmp_path / "evalkit.yaml"
    paths = "cases: eval/cases\nfixtures: eval/fixtures\n"
    config.write_text(config.read_text().replace(paths, ""))
    lines: list[str] = []
    with pytest.raises(ApiRequestError, match="finance@admin"):
        await arun_experiment(
            dataset="finance",
            agent="pushed",
            task=lambda q: RunResult(answer="x", trajectory=DEFAULT_TRAJECTORY),
            progress=lines.append,
        )
    assert any("no cases: directory" in line for line in lines)


async def test_dry_run_session_lists_with_case_title_and_id(env):
    srv, blob, provider = env
    base_url = srv.base_url
    tracer = provider.get_tracer("client")

    def task(input: dict) -> str:
        with tracer.start_as_current_span("llm", attributes=LLM_ATTRS):
            pass
        return "42"

    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=task,
        tracer_provider=provider,
        case_ids=["case-2"],
        dry_run=True,
        poll_interval=0.2,
    )
    [listed] = runs_page(base_url, result.runs[0].session_id)
    assert listed["title"] == "q2" and listed["case_id"] == "case-2"
    assert listed["experiment_id"] is None


async def test_dry_run_reports_a_trajectory_read_failure_and_goes_on(env, monkeypatch):
    srv, blob, provider = env

    async def failing_poll(sessions, agent, session_id, *args, **kwargs):
        raise ApiRequestError(502, "upstream", "GET", f"/api/agents/{agent}/sessions/{session_id}")

    monkeypatch.setattr(sdk_runner, "poll_trajectory", failing_poll)
    lines: list[str] = []
    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=lambda q: RunResult(answer="42", session_id=f"s-{q['question']}"),
        tracer_provider=provider,
        dry_run=2,
        progress=lines.append,
    )
    assert [(r.case_id, r.trajectory_status) for r in result.runs] == [
        ("case-1", "unavailable"),
        ("case-2", "unavailable"),
    ]
    failures = [line for line in lines if "trajectory read failed" in line]
    assert len(failures) == 2 and "502" in failures[0] and "s-q1" in failures[0]


async def test_model_override_warns_when_the_task_cannot_receive_it(env):
    srv, blob, provider = env
    common = {
        "dataset": "finance",
        "workspace": "canonical",
        "agent": "pushed",
        "tracer_provider": provider,
        "dry_run": True,
        "model": "gpt-x",
    }
    warned: list[str] = []
    await arun_experiment(
        **common,
        task=lambda q: RunResult(answer="dry", trajectory=DEFAULT_TRAJECTORY),
        progress=warned.append,
    )
    assert [line for line in warned if "model=gpt-x" in line] == [
        "warning: model=gpt-x is recorded on the experiment, but the task has no `model` "
        "parameter, so the agent never sees it"
    ]
    seen: list[str | None] = []

    def task(input: dict, model: str | None) -> RunResult:
        seen.append(model)
        return RunResult(answer="dry", trajectory=DEFAULT_TRAJECTORY)

    quiet: list[str] = []
    await arun_experiment(**common, task=task, progress=quiet.append)
    assert seen == ["gpt-x"] and not [line for line in quiet if "model=" in line]


async def test_failed_scoring_finishes_the_other_runs_and_says_how_to_finish(env, monkeypatch):
    srv, blob, provider = env
    store = srv.app.state.store
    real_publish = store.publish_evaluations
    failed: list[int] = []

    async def publish_failing_once(experiment_run_id, rows, *, force, holder=None):
        if not failed:
            failed.append(experiment_run_id)
            raise RuntimeError("store down")
        await real_publish(experiment_run_id, rows, force=force, holder=holder)

    monkeypatch.setattr(store, "publish_evaluations", publish_failing_once)
    lines: list[str] = []
    with pytest.raises(ScoringFailed) as raised:
        await arun_experiment(
            dataset="finance",
            workspace="canonical",
            agent="pushed",
            task=lambda input: RunResult(answer="42", trajectory=DEFAULT_TRAJECTORY),
            repetitions=2,
            progress=lines.append,
        )
    exp_id = raised.value.exp_id
    hint = (
        f"experiment {exp_id} is not fully scored; finish it with "
        f"evalkit.sdk.evaluate_experiment(experiment_id={exp_id}) or `evalkit evaluate {exp_id}`"
    )
    assert hint in lines and raised.value.__notes__ == [hint]
    assert "scoring failed for 1 of 4 runs" in str(raised.value)
    exp, runs = await srv.experiment(exp_id)
    assert exp.status == "failed"
    assert [bool(run.evaluations) for run in runs].count(True) == 3

    monkeypatch.setattr(store, "publish_evaluations", real_publish)
    await aevaluate_experiment(experiment_id=exp_id, progress=lines.append)
    exp, runs = await srv.experiment(exp_id)
    assert all(run.evaluations for run in runs)
    assert exp.status == "completed"


async def test_a_finished_run_prints_its_scorecard(env):
    srv, blob, provider = env
    lines: list[str] = []
    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: RunResult(answer="pushed", trajectory=DEFAULT_TRAJECTORY),
        tracer_provider=provider,
        case_ids=["case-1"],
        trajectory_wait_seconds=0,
        progress=lines.append,
    )
    assert any(line.startswith(f"\nexperiment {result.experiment_id} ") for line in lines)
    assert "evaluators:" in lines


async def test_sdk_runs_skip_keyless_evaluators_their_cases_do_not_use(env):
    srv, blob, provider = env
    created = httpx.post(
        f"{srv.base_url}/api/evaluators",
        headers={"Authorization": f"Bearer {os.environ['EVALKIT_TOKEN']}"},
        json={
            "workspace": "admin",
            "name": "nokey_eval",
            "kind": "score",
            "settings": {"endpoint": "http://e"},
        },
    )
    assert created.status_code == 200, created.text
    lines: list[str] = []
    await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: RunResult(answer="x", trajectory=DEFAULT_TRAJECTORY),
        case_ids=["case-1"],
        tracer_provider=provider,
        progress=lines.append,
    )
    assert "experiment" in lines[0]
    assert not any("nokey_eval" in line for line in lines)
