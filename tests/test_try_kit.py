import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from typer.testing import CliRunner

from evalkit.cli.main import app
from evalkit.config import LocalConfig, load_config
from evalkit.contracts.atif import validate_trajectory
from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.convert import to_atif
from evalkit.core.traces.mapping import to_esf
from evalkit.core.traces.raw import RawSpan
from evalkit.credentials import CredentialRegistry

ROOT = Path(__file__).parent.parent
KIT = ROOT / "skills" / "evalkit-deployment" / "try"
GEN_AI = ROOT / "skills" / "connect-traces" / "references" / "gen_ai.json"
KIT_MAPPING = KIT / "mapping.json"
LLM_ENV = {"LLM_BASE_URL": "https://llm.test/v1", "LLM_API_KEY": "k", "LLM_MODEL": "m"}


def load_agent_module():
    spec = importlib.util.spec_from_file_location("try_agent", KIT / "agent.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fake_llm(requests: list[dict]) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "lookup_financials",
                            "arguments": '{"metric": "revenue_fy2025"}',
                        },
                    }
                ],
            }
        else:
            message = {"role": "assistant", "content": "Revenue was 48.2 million in FY2025."}
        return httpx.Response(
            200,
            json={
                "choices": [{"message": message}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    return httpx.MockTransport(handle)


def raw(span: ReadableSpan) -> RawSpan:
    context = span.get_span_context()
    assert context is not None and span.start_time is not None and span.end_time is not None
    return RawSpan(
        trace_id=f"{context.trace_id:032x}",
        span_id=f"{context.span_id:016x}",
        parent_span_id=f"{span.parent.span_id:016x}" if span.parent else None,
        name=span.name,
        start_time=datetime.fromtimestamp(span.start_time / 1e9, UTC),
        end_time=datetime.fromtimestamp(span.end_time / 1e9, UTC),
        attributes=dict(span.attributes or {}),
    )


def test_sample_agent_spans_convert_to_a_trajectory_with_its_tool_call(monkeypatch):
    for name, value in LLM_ENV.items():
        monkeypatch.setenv(name, value)
    module = load_agent_module()
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(module, "tracer", provider.get_tracer("test"))
    requests: list[dict] = []
    client = httpx.Client(base_url=LLM_ENV["LLM_BASE_URL"], transport=fake_llm(requests))

    answer = module.Analyst(client).ask("What was Acme Widgets' revenue in FY2025?")

    assert answer == "Revenue was 48.2 million in FY2025."
    assert requests[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "48.2 million",
    }
    mapping = MappingDocument.model_validate_json(KIT_MAPPING.read_text())
    spans = [to_esf(raw(s), mapping) for s in exporter.get_finished_spans()]
    trajectory = validate_trajectory(
        to_atif(spans, mapping, agent_name="analyst", session_id="s", mapping_version=1).model_dump(
            exclude_none=True
        )
    )
    calls = [c for step in trajectory.steps for c in step.tool_calls or []]
    assert [(c.function_name, c.arguments) for c in calls] == [
        ("lookup_financials", {"metric": "revenue_fy2025"})
    ]
    results = [r.content for s in trajectory.steps if s.observation for r in s.observation.results]
    assert results == ["48.2 million"]
    assert trajectory.steps[-1].message == answer
    assert trajectory.final_metrics is not None
    assert trajectory.final_metrics.total_prompt_tokens == 200


def test_kit_setup_registers_the_llm_as_the_default_judge(monkeypatch):
    monkeypatch.setenv("EVALKIT_ENCRYPTION_KEY", "k")
    compose = (KIT / "docker-compose.yml").read_text()
    assert (
        'evalkit llm-providers create try --base-url "$$LLM_BASE_URL" --api-key-env LLM_API_KEY'
        in compose
    )
    assert 'evalkit judge-default set --provider try --model "$$LLM_MODEL"' in compose
    cfg = load_config(KIT / "config.yaml")
    assert cfg.allow_private_endpoints is True and cfg.encryption_key


def test_kit_local_config_reads_the_same_llm(monkeypatch):
    for name, value in LLM_ENV.items():
        monkeypatch.setenv(name, value)
    cfg = LocalConfig.find(KIT)
    assert cfg is not None
    assert cfg.cases == KIT / "cases"
    registry = CredentialRegistry(cfg.credentials, cfg.environ(), home="admin")
    assert registry.require_judge_key(LLM_ENV["LLM_BASE_URL"]) == "k"


def test_kit_env_example_names_every_variable_compose_needs():
    names = {
        line.split("=", 1)[0]
        for line in (KIT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    }
    compose = (KIT / "docker-compose.yml").read_text()
    assert names == {"LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL", "EVALKIT_ADMIN_PASSWORD"}
    assert all(f"${{{name}:?" in compose for name in names)


def test_kit_cases_lint_clean():
    result = CliRunner().invoke(app, ["lint", "--offline", str(KIT / "cases")])
    assert result.exit_code == 0, result.output


def test_kit_mapping_starts_as_the_connect_traces_gen_ai_mapping():
    assert json.loads(KIT_MAPPING.read_text()) == json.loads(GEN_AI.read_text())


def test_kit_server_migrates_on_start_and_run_py_is_pinned_on_copy(monkeypatch):
    monkeypatch.setenv("EVALKIT_ENCRYPTION_KEY", "k")
    assert load_config(KIT / "config.yaml").db.migrate_on_start is True
    assert "migrate:" not in (KIT / "docker-compose.yml").read_text()
    header = (KIT / "run.py").read_text().split("# ///\n")[0]
    assert 'dependencies = ["evalkit-py[otel]"]' in header
    assert "tool.uv" not in header


def test_run_py_help_prints_usage_without_starting_a_run(tmp_path):
    result = subprocess.run(
        [sys.executable, str(KIT / "run.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "EVALKIT_SERVER_URL": "http://127.0.0.1:9", "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout
