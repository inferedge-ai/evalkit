import json

import httpx
import pytest
import respx
from typer.testing import CliRunner

from evalkit.cli import main as main_module
from evalkit.cli.client import ApiClient
from evalkit.cli.main import app as cli_app
from evalkit.cli.traces_commands import render_check
from evalkit.client import connection
from evalkit.client.api import AsyncApi
from evalkit.sdk.runner import _session_summary

SESSION = {
    "settled": True,
    "root_present": True,
    "span_count": 3,
    "mapping_version": 3,
    "unmapped_attributes": ["llm.prompt_template.version", "a", "b", "c", "d"],
    "trajectory": {
        "schema_version": "ATIF-v1.8",
        "steps": [
            {
                "step_id": 1,
                "source": "agent",
                "tool_calls": [{"tool_call_id": "x", "function_name": "f", "arguments": {}}],
            }
        ],
        "subagent_trajectories": None,
    },
    "esf": [
        {
            "span_id": "r",
            "trace_id": "t",
            "parent_id": None,
            "name": "evalkit.run",
            "kind": "chain",
            "started_at": "2026-10-09T00:00:00Z",
            "ended_at": "2026-10-09T00:00:09Z",
            "error": None,
        },
        {
            "span_id": "l",
            "trace_id": "t",
            "parent_id": "r",
            "name": "llm",
            "kind": "llm",
            "started_at": "2026-10-09T00:00:01Z",
            "ended_at": "2026-10-09T00:00:02Z",
            "error": None,
            "model": "m",
            "output_messages": [{"role": "assistant", "text": "x"}],
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "cached_tokens": 4,
        },
        {
            "span_id": "t1",
            "trace_id": "t",
            "parent_id": "r",
            "name": "lookup",
            "kind": "tool",
            "started_at": "2026-10-09T00:00:03Z",
            "ended_at": "2026-10-09T00:00:04Z",
            "error": None,
            "tool_call_id": "x",
        },
    ],
}


def test_render_check_lines():
    lines, ok = render_check(SESSION)
    assert ok
    assert lines[0].startswith("  ✓ session        3 spans in 1 trace") and "settled" in lines[0]
    assert lines[1] == '  ✓ root           "evalkit.run" (chain), ended'
    assert lines[2] == "  ✓ mapping        v3"
    assert lines[3] == "  ✓ llm            1 spans, model m, tokens in=10 out=2 cached=4"
    assert lines[4] == "  ✓ tools          1 spans, 1 matched to calls by id"
    assert lines[5] == "  ! unmapped       llm.prompt_template.version, a (+3)"
    assert lines[6] == "  ✓ trajectory     ATIF-v1.8, 1 steps, 1 tool calls, 0 subagents"


def test_render_check_without_root():
    session = {**SESSION, "root_present": False, "esf": SESSION["esf"][1:], "mapping_version": None}
    lines, ok = render_check(session)
    assert not ok and lines[1].startswith("  ✗ root")
    assert lines[2] == "  ! mapping        no active mapping; the agent's spans are unmapped"


def test_render_check_warns_on_tool_spans_in_no_step():
    sub = {"schema_version": "ATIF-v1.8", "steps": [], "extra": {"unplaced_tool_spans": ["ls"]}}
    trajectory = {**SESSION["trajectory"], "subagent_trajectories": [sub]}
    lines, _ = render_check({**SESSION, "trajectory": trajectory})
    assert lines[4] == "  ! tools          1 spans, 1 matched to calls by id, 1 in no step (ls)"


def test_render_check_warns_on_llm_spans_without_messages():
    esf = [{**s, "output_messages": None} for s in SESSION["esf"]]
    lines, _ = render_check({**SESSION, "esf": esf})
    assert lines[3].startswith("  ! llm            1 spans") and lines[3].endswith(
        "1 without messages (is message content capture on?)"
    )


PROXY_502 = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


@pytest.fixture
def proxy_down(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            return httpx.Response(200, json={"username": "akshat"})
        return httpx.Response(502, text=PROXY_502, headers={"content-type": "text/html"})

    monkeypatch.setattr(
        connection, "active", lambda path=None: connection.Connection("http://t", "tok", None)
    )
    monkeypatch.setattr(
        main_module,
        "ApiClient",
        lambda url, token: ApiClient(url, token, transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    ("args", "line"),
    [
        (
            ["traces", "check", "research-agent", "abc"],
            "GET /api/agents/research-agent@akshat/sessions/abc/trajectory → 502: Bad Gateway",
        ),
        (["status", "1"], "GET /api/experiments/1 → 502: Bad Gateway"),
    ],
)
def test_server_502_exits_1_with_a_status_line(proxy_down, args, line):
    result = CliRunner().invoke(main_module.app, args)
    assert result.exit_code == 1
    assert result.output.strip() == line


@respx.mock
def test_traces_check_prints_the_size_limit_on_413(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setenv("EVALKIT_SERVER_URL", "http://srv")
    monkeypatch.setenv("EVALKIT_TOKEN", "tok")
    detail = "session big of agent sdk holds 900.0 MiB of span attributes, over this server's "
    respx.get("http://srv/api/auth/me").mock(
        return_value=httpx.Response(200, json={"username": "admin"})
    )
    respx.get("http://srv/api/agents/sdk@canonical/sessions/big/trajectory").mock(
        return_value=httpx.Response(413, json={"detail": detail})
    )
    result = CliRunner().invoke(cli_app, ["traces", "check", "sdk@canonical", "big"])
    assert result.exit_code == 1
    assert "413" in result.output and "900.0 MiB" in result.output


@respx.mock
async def test_dry_run_session_summary_shows_the_size_limit_on_413():
    respx.get("http://srv/s").mock(
        return_value=httpx.Response(413, json={"detail": "holds 900.0 MiB, over the limit"})
    )
    api = AsyncApi("http://srv", "tok")
    assert (
        await _session_summary(api, "/s", "big") == "  session big: holds 900.0 MiB, over the limit"
    )
    await api.aclose()


def test_render_check_does_not_flag_later_llm_spans_whose_input_was_not_loaded():
    first = {**SESSION["esf"][1], "input_messages": [{"role": "user", "text": "q"}]}
    later = {**SESSION["esf"][1], "span_id": "l2", "input_messages": []}
    bare = {**later, "span_id": "l3", "output_messages": []}
    lines, _ = render_check({**SESSION, "esf": [SESSION["esf"][0], first, later]})
    assert lines[3].startswith("  ✓ llm            2 spans")
    lines, _ = render_check({**SESSION, "esf": [SESSION["esf"][0], first, later, bare]})
    assert lines[3].startswith("  ! llm            3 spans") and "1 without messages" in lines[3]


def test_render_check_warns_when_llm_spans_carry_no_token_counts():
    esf = [
        {**span, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
        if span["kind"] == "llm"
        else span
        for span in SESSION["esf"]
    ]
    lines, _ = render_check({**SESSION, "esf": esf})
    llm = next(line for line in lines if " llm " in line)
    assert llm.startswith("  ! llm") and "no token counts (check the tokens rule)" in llm


def test_mapping_push_with_a_missing_file_is_a_one_line_error(tmp_path):
    result = CliRunner().invoke(
        cli_app,
        ["traces", "mapping", "push", "myagent", str(tmp_path / "nope.json")],
        env={"COLUMNS": "200"},
    )
    assert result.exit_code == 2
    assert "does not exist" in result.output
    assert "Traceback" not in result.output


def import_view(status: str, reached: str = "2026-10-03T00:00:00Z", **extra) -> dict:
    return {
        "id": 12,
        "agent": "sdk@canonical",
        "source": "phx",
        "project": "research-agent",
        "since": "2026-10-01T00:00:00Z",
        "until": "2026-10-05T00:00:00Z",
        "resume_from": reached,
        "status": status,
        "traces": 40,
        "spans": 800,
        "sample_session": None,
        "error": None,
        "created_by": "admin",
        "created_at": "2026-10-15T00:00:00Z",
        "finished_at": None,
        **extra,
    }


@pytest.fixture
def srv(tmp_path, monkeypatch):
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setenv("EVALKIT_SERVER_URL", "http://srv")
    monkeypatch.setenv("EVALKIT_TOKEN", "tok")
    monkeypatch.setattr("evalkit.cli.traces_commands.time.sleep", lambda _: None)
    with respx.mock(assert_all_called=False) as mock:
        mock.get("http://srv/api/auth/me").mock(
            return_value=httpx.Response(200, json={"username": "admin"})
        )
        mock.get("http://srv/api/health").mock(return_value=httpx.Response(200, json={"ok": True}))
        yield mock


IMPORTS = "http://srv/api/agents/sdk@canonical/imports"


def test_range_import_starts_follows_and_checks_the_sample_session(srv):
    started = srv.post(IMPORTS).mock(return_value=httpx.Response(202, json=import_view("running")))
    srv.get(f"{IMPORTS}/12").mock(
        side_effect=[
            httpx.Response(200, json=import_view("running")),
            httpx.Response(200, json=import_view("running")),
            httpx.Response(
                200, json=import_view("done", "2026-10-04T12:00:00Z", sample_session="s9")
            ),
        ]
    )
    srv.get("http://srv/api/meta").mock(return_value=httpx.Response(200, json={}))
    srv.get("http://srv/api/agents/sdk@canonical/sessions/s9/trajectory").mock(
        return_value=httpx.Response(200, json=SESSION)
    )
    args = ["traces", "import", "sdk@canonical", "--source", "phx", "--project", "research-agent"]
    result = CliRunner().invoke(cli_app, [*args, "--since", "2026-10-01", "--until", "2026-10-05"])
    assert result.exit_code == 0, result.output
    body = json.loads(started.calls[0].request.content)
    assert body == {
        "source": "phx",
        "project": "research-agent",
        "since": "2026-10-01T00:00:00+00:00",
        "until": "2026-10-05T00:00:00+00:00",
    }
    lines = result.output.splitlines()
    assert lines[0].startswith("import 12 started")
    assert lines[1] == "12   running  2026-10-03 00:00   50%  40 traces  800 spans"
    assert lines[2] == "12   done     2026-10-04 12:00  100%  40 traces  800 spans"
    assert lines[3] == "check of session s9:"
    assert lines[4].startswith("  ✓ session")


def test_a_failed_import_prints_how_to_continue(srv):
    srv.get(f"{IMPORTS}/12").mock(
        return_value=httpx.Response(200, json=import_view("failed", error="HTTPStatusError: 500"))
    )
    result = CliRunner().invoke(cli_app, ["traces", "imports", "sdk@canonical", "12"])
    assert result.exit_code == 1
    assert "failed: HTTPStatusError: 500" in result.output
    assert "evalkit traces imports sdk@canonical 12 --continue" in result.output


def test_imports_lists_stops_and_continues(srv):
    srv.get(IMPORTS).mock(return_value=httpx.Response(200, json=[import_view("done")]))
    stop = srv.post(f"{IMPORTS}/12/stop").mock(
        return_value=httpx.Response(200, json=import_view("stopping"))
    )
    resume = srv.post(f"{IMPORTS}/12/continue").mock(
        return_value=httpx.Response(200, json=import_view("running"))
    )
    srv.get(f"{IMPORTS}/12").mock(return_value=httpx.Response(200, json=import_view("done")))
    srv.get("http://srv/api/meta").mock(return_value=httpx.Response(200, json={}))
    listed = CliRunner().invoke(cli_app, ["traces", "imports", "sdk@canonical"])
    assert listed.exit_code == 0 and "phx/research-agent  by admin" in listed.output
    stopped = CliRunner().invoke(cli_app, ["traces", "imports", "sdk@canonical", "12", "--stop"])
    assert stopped.exit_code == 0 and stop.called and "stopping" in stopped.output
    continued = CliRunner().invoke(
        cli_app, ["traces", "imports", "sdk@canonical", "12", "--continue"]
    )
    assert continued.exit_code == 0 and resume.called
    assert "no sessions were imported" in continued.output


@pytest.mark.parametrize(
    "extra",
    [["abc", "--since", "2026-10-01"], [], ["abc", "--until", "2026-10-05"]],
)
def test_import_needs_exactly_one_of_a_session_or_since(srv, extra):
    args = ["traces", "import", "sdk@canonical", "--source", "phx", "--project", "p", *extra]
    result = CliRunner().invoke(cli_app, args, env={"COLUMNS": "200"})
    assert result.exit_code == 2
