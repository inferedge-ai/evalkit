import json
import time

import httpx
import pytest
from cryptography.fernet import Fernet

from evalkit.db.crypto import SecretBox
from evalkit.db.trace_sources import TraceSourceStore
from tests.fake_agents import reference_mapping
from tests.flows.helpers_12_17 import DEV, live_stack


@pytest.fixture
def live(db_url, tmp_path, monkeypatch, stub_judges):
    with live_stack(db_url, tmp_path, monkeypatch) as stack:
        yield stack


def finished(client: httpx.Client, exp_id: int) -> dict:
    for _ in range(400):
        body = client.get(f"/api/experiments/{exp_id}").json()
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"experiment {exp_id} stuck in {body['status']}")


def test_flow_17_agents_create_edit_show_list_archive(live, tmp_path):
    created = live.run(
        "agents",
        "create",
        "ouragent2",
        "--workspace",
        DEV,
        "--harness-url",
        "http://h",
        "--repo-url",
        "https://git/x",
        "--max-concurrent-runs",
        "3",
    )
    assert created.exit_code == 0, created.output
    assert "ouragent2@dev  (shared, driven by server)" in created.output
    mine = live.run("agents", "create", "mine", "--harness-url", "http://h")
    assert "mine@admin  (personal, driven by cli)" in mine.output
    sdk = live.run("agents", "create", "coded")
    assert "coded@admin  (personal, driven by sdk)" in sdk.output
    mapping = tmp_path / "m.json"
    mapping.write_text(json.dumps(reference_mapping()))
    traced = live.run(
        "agents",
        "create",
        "traced",
        "--workspace",
        DEV,
        "--trajectory-source",
        "otel",
        "--mapping",
        str(mapping),
    )
    assert traced.exit_code == 0, traced.output
    admin = live.http()
    mappings = admin.get(f"/api/agents/traced@{DEV}/mappings").json()
    assert [(m["version"], m["status"]) for m in mappings] == [(1, "active")]
    no_mapping = live.run("agents", "create", "nomap", "--trajectory-source", "otel")
    assert no_mapping.exit_code == 1 and "mapping" in no_mapping.output
    assert (
        admin.post(
            "/api/agents", json={"workspace": DEV, "name": "nomap2", "trajectory_source": "otel"}
        ).status_code
        == 400
    )
    duplicate = admin.post("/api/agents", json={"workspace": DEV, "name": "ouragent2"})
    assert duplicate.status_code == 409
    negative = admin.post(
        "/api/agents",
        json={"workspace": DEV, "name": "neg", "settings": {"run_timeout_seconds": -1}},
    )
    assert negative.status_code in (400, 422) and "run_timeout_seconds" in negative.text
    provides = live.run("agents", "edit", f"ouragent2@{DEV}", "--provides", "trajectory=true")
    assert provides.exit_code == 1 and "provides" in provides.output
    edited = live.run("agents", "edit", f"ouragent2@{DEV}", "--trajectory-wait", "9")
    assert edited.exit_code == 0, edited.output
    shown = live.run("agents", "show", f"ouragent2@{DEV}").output
    assert "trajectory_wait_seconds 9" in shown and "max_concurrent_runs 3" in shown
    assert "https://git/x" in shown
    listed = live.run("agents", "list", "--workspace", DEV).output
    assert "ouragent2@dev" in listed and "mine@admin" not in listed
    assert live.run("agents", "archive", f"ouragent2@{DEV}").exit_code == 0
    assert "ouragent2@dev" not in live.run("agents", "list").output
    assert "ouragent2@dev" in live.run("agents", "list", "--archived").output
    run = admin.post(
        "/api/experiments", json={"dataset": f"finance@{DEV}", "agent": f"ouragent2@{DEV}"}
    )
    assert run.status_code == 409
    missing = admin.get(f"/api/agents/nope@{DEV}")
    assert missing.status_code == 404


def test_flow_17_write_permissions_and_open_reads(live):
    alice = live.http("alice")
    agent = {"workspace": DEV, "name": "a", "harness_url": "http://h"}
    assert alice.post("/api/agents", json=agent).status_code == 403
    assert alice.post("/api/agents", json={**agent, "workspace": "akshat"}).status_code == 403
    assert alice.post("/api/agents", json={**agent, "workspace": "alice"}).status_code == 200
    assert live.http().post("/api/agents", json={**agent, "workspace": "akshat"}).status_code == 403
    assert alice.get("/api/agents/myagent@akshat").status_code == 200
    assert alice.get(f"/api/agents/ouragent@{DEV}").status_code == 200
    shared_run = {
        "dataset": f"finance@{DEV}",
        "agent": f"ouragent@{DEV}",
        "selection": {"case_ids": ["case-1"]},
    }
    not_a_member = alice.post("/api/experiments", json=shared_run)
    assert not_a_member.status_code == 403
    started = live.http().post("/api/experiments", json=shared_run)
    assert started.status_code == 200, started.text
    foreign = alice.post(
        "/api/experiments",
        json={
            "dataset": f"finance@{DEV}",
            "agent": "myagent@akshat",
            "capabilities": live.fake.capabilities,
            "holder": "cli:alice@x:1",
        },
    )
    assert foreign.status_code == 403
    unknown = live.http().post(
        "/api/experiments", json={"dataset": f"finance@{DEV}", "agent": f"ghost@{DEV}"}
    )
    assert unknown.status_code == 404
    assert "create it with evalkit agents create" in unknown.json()["detail"]


def test_flow_17_remote_evaluators_and_the_experiment_snapshot(live):
    created = live.run(
        "evaluators",
        "create",
        "compliance",
        "--workspace",
        DEV,
        "--endpoint",
        "http://e/eval",
        "--kind",
        "gauge",
        "--timeout",
        "30",
    )
    assert created.exit_code == 0, created.output
    assert "compliance@dev (gauge)" in created.output
    listed = live.run("evaluators", "list", "--workspace", DEV).output
    assert "compliance@dev" in listed and "http://e/eval" in listed
    assert (
        live.http()
        .post(
            "/api/evaluators",
            json={
                "workspace": DEV,
                "name": "compliance",
                "kind": "score",
                "settings": {"endpoint": "x"},
            },
        )
        .status_code
        == 409
    )
    alice = live.http("alice")
    assert (
        alice.post(
            "/api/evaluators",
            json={"workspace": DEV, "name": "x", "kind": "score", "settings": {"endpoint": "x"}},
        ).status_code
        == 403
    )

    admin = live.http()
    exp = admin.post(
        "/api/experiments",
        json={
            "dataset": f"finance@{DEV}",
            "agent": f"ouragent@{DEV}",
            "selection": {"case_ids": ["case-1"]},
        },
    ).json()
    done = finished(admin, exp["id"])
    snapshot = done["config"]["snapshot"]["remote_evaluators"]
    assert snapshot["compliance"] == {"endpoint": "http://e/eval", "timeout_seconds": 30}
    assert "required_tool_calls" in snapshot
    runs = admin.get(f"/api/experiments/{exp['id']}/runs").json()
    names = {e["name"]: e for r in runs for e in r["evaluations"]}
    assert names["required_tool_calls"]["source"] == "remote"
    assert names["required_tool_calls"]["error"] is None

    edited = live.run("evaluators", "edit", f"compliance@{DEV}", "--timeout", "99")
    assert edited.exit_code == 0, edited.output
    assert live.run("evaluators", "archive", f"compliance@{DEV}").exit_code == 0
    assert "compliance" not in live.run("evaluators", "list", "--workspace", DEV).output
    again = admin.get(f"/api/experiments/{exp['id']}").json()["config"]["snapshot"]
    assert again["remote_evaluators"]["compliance"]["timeout_seconds"] == 30


def test_flow_17_trace_sources_are_admin_only_and_hide_keys(live, monkeypatch):
    live.app.state.trace_sources = TraceSourceStore(
        live.app.state.session_factory, SecretBox(Fernet.generate_key().decode())
    )
    monkeypatch.setenv("PHX_KEY", "very-secret")
    created = live.run(
        "trace-sources", "create", "phx", "--url", "https://phx", "--api-key-env", "PHX_KEY"
    )
    assert created.exit_code == 0, created.output
    listed = live.run("trace-sources", "list").output
    assert listed.split()[:3] == ["phx", "phoenix", "https://phx"]
    assert "very-secret" not in json.dumps(live.http().get("/api/trace-sources").json())
    assert live.run("trace-sources", "edit", "phx", "--url", "https://phx2").exit_code == 0
    assert "https://phx2" in live.run("trace-sources", "list").output
    live.as_user("alice")
    assert live.run("trace-sources", "create", "x", "--url", "u").exit_code == 1
    assert live.run("trace-sources", "archive", "phx").exit_code == 1
    live.as_user("admin")
    assert live.run("trace-sources", "archive", "phx").exit_code == 0


def test_flow_17_credentials_come_from_the_drivers_map(live):
    admin = live.http()
    assert (
        admin.post(
            "/api/agents", json={"workspace": DEV, "name": "nokey", "harness_url": live.harness_url}
        ).status_code
        == 200
    )
    no_key = admin.post(
        "/api/experiments",
        json={"dataset": f"finance@{DEV}", "agent": f"nokey@{DEV}"},
    )
    assert no_key.status_code == 400
    assert no_key.json()["detail"] == (
        f"no credentials.agents entry for nokey@{DEV} in the server config"
    )
    judged = admin.post(
        "/api/experiments",
        json={
            "dataset": f"finance@{DEV}",
            "agent": f"ouragent@{DEV}",
            "judge": {"base_url": "http://nokey-judge.test", "model": "m"},
        },
    )
    assert judged.status_code == 400
    assert judged.json()["detail"] == "no credentials.judges entry for http://nokey-judge.test"
