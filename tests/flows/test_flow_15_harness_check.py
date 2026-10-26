import pytest

from tests.flows.helpers_12_17 import DEV, live_stack


@pytest.fixture
def live(db_url, tmp_path, monkeypatch, stub_judges):
    with live_stack(db_url, tmp_path, monkeypatch) as stack:
        yield stack


def entries(output: str) -> dict[str, str]:
    marks = {"✓": "pass", "!": "warn", "✗": "fail", "-": "skipped"}
    found = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] in marks:
            found[parts[1]] = marks[parts[0]]
    return found


def write_local_config(tmp_path) -> None:
    (tmp_path / "evalkit.yaml").write_text(
        "credentials:\n  agents:\n    myagent: env:MY_AGENT_KEY\n"
    )
    (tmp_path / ".env").write_text("MY_AGENT_KEY=client-key\n")


def test_flow_15_server_checks_a_shared_agent(live):
    live.fake.capabilities.update({"revision": "abc123", "branch": "main"})
    result = live.run("harness", "check", f"ouragent@{DEV}")
    assert result.exit_code == 0, result.output
    found = entries(result.output)
    assert found["capabilities"] == "pass" and "abc123" in result.output
    assert found["input_schema"] == "pass" and found["run"] == "pass"
    assert found["files"] == "pass" and found["trajectory"] == "pass"
    assert found["workspace"] == "pass" and found["run_listing"] == "pass"
    assert found["stop"] in ("pass", "warn")
    alice = live.http("alice")
    report = alice.post(f"/api/agents/ouragent@{DEV}/check")
    assert report.status_code == 200 and report.json()["checks"]["run"]["status"] == "pass"
    assert alice.post("/api/agents/myagent@akshat/check").status_code == 403
    assert alice.post(f"/api/agents/ghost@{DEV}/check").status_code == 404


def test_flow_15_undeclared_capabilities_are_skipped(live):
    live.fake.capabilities["provides"].update(
        {"trajectory": False, "workspace": False, "run_listing": False}
    )
    live.fake.capabilities["accepts"].update({"files": False, "file_urls": False, "stop": False})
    result = live.run("harness", "check", f"ouragent@{DEV}")
    assert result.exit_code == 0, result.output
    found = entries(result.output)
    for capability in ("files", "trajectory", "workspace", "run_listing", "stop"):
        assert found[capability] == "skipped", capability


def test_flow_15_missing_credential_fails_capabilities_and_stops(live):
    admin = live.http()
    admin.post("/api/agents", json={"workspace": DEV, "name": "nokey", "harness_url": "http://h"})
    result = live.run("harness", "check", f"nokey@{DEV}")
    assert result.exit_code == 1
    found = entries(result.output)
    assert found == {"capabilities": "fail"} and "401" in result.output


def test_flow_15_own_agent_is_checked_from_the_laptop(live, tmp_path, monkeypatch):
    write_local_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    live.as_user("akshat")
    result = live.run("harness", "check", "myagent")
    assert result.exit_code == 0, result.output
    assert entries(result.output)["run"] == "pass"
    live.http("akshat").post("/api/agents", json={"workspace": "akshat", "name": "coded"})
    coded = live.run("harness", "check", "coded")
    assert coded.exit_code == 1 and "code-driven agent" in coded.output
    live.as_user("admin")
    foreign = live.run("harness", "check", "myagent@akshat")
    assert foreign.exit_code == 1
    assert "only its owner can check it" in foreign.output
    served = live.http("akshat").post("/api/agents/myagent@akshat/check")
    assert served.status_code == 400 and "does not drive" in served.json()["detail"]


def test_flow_15_lint_reads_a_personal_agents_schema_from_the_laptop(live, tmp_path, monkeypatch):
    write_local_config(tmp_path)
    monkeypatch.chdir(tmp_path)
    cases = tmp_path / "lintcases"
    cases.mkdir()
    (cases / "c.yaml").write_text("- id: l15-1\n  input: {question: 'q as of 2024-01-01'}\n")
    live.fake.capabilities["input_schema"] = {"type": "object", "required": ["ticker"]}
    live.as_user("akshat")
    try:
        result = live.run("lint", str(cases), "--agent", "myagent")
    finally:
        del live.fake.capabilities["input_schema"]
    assert result.exit_code == 1, result.output
    assert "E-INPUT-SCHEMA" in result.output and "l15-1" in result.output
