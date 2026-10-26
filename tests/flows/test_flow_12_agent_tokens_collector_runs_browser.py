import httpx
import pytest

from tests.api.test_otel import PROTO, otlp_payload
from tests.fake_agents import reference_mapping
from tests.flows.helpers_12_17 import DEV, live_stack


@pytest.fixture
def live(db_url, tmp_path, monkeypatch, stub_judges):
    with live_stack(db_url, tmp_path, monkeypatch) as stack:
        admin = stack.http()
        created = admin.post(
            "/api/agents",
            json={
                "workspace": DEV,
                "name": "deployed",
                "trajectory_source": "otel",
                "mapping": reference_mapping(),
            },
        )
        assert created.status_code == 200, created.text
        yield stack


def bearer(live, token: str) -> httpx.Client:
    return httpx.Client(base_url=live.url, headers={"Authorization": f"Bearer {token}"})


def export(client: httpx.Client, session_id: str, agent: str | None = None) -> httpx.Response:
    headers = {**PROTO, **({"X-Evalkit-Agent": agent} if agent else {})}
    return client.post("/api/otel/v1/traces", content=otlp_payload(session_id), headers=headers)


def test_flow_12_agent_token_exports_only_its_agent_and_dies_on_revoke(live):
    created = live.run("tokens", "create", "--agent", f"deployed@{DEV}")
    assert created.exit_code == 0, created.output
    header, secret = created.output.strip().splitlines()[-2:]
    assert f"for deployed@{DEV} (shown once)" in header
    listed = live.run("tokens", "list").output
    assert f"deployed@{DEV}" in listed and secret not in listed
    bound = bearer(live, secret)
    assert export(bound, "s-1").status_code == 200
    session = live.http().get(f"/api/agents/deployed@{DEV}/sessions/s-1/trajectory").json()
    assert session["span_count"] == 2 and session["root_present"]
    other = export(bound, "s-2", f"ouragent@{DEV}")
    assert other.status_code == 403
    assert f"token is bound to deployed@{DEV}" in other.json()["detail"]
    elsewhere = bound.get("/api/datasets")
    assert elsewhere.status_code == 403
    assert elsewhere.json()["detail"] == "agent tokens may only export traces"
    token_id = listed.split()[0]
    assert live.run("tokens", "revoke", token_id).exit_code == 0
    assert export(bound, "s-3").status_code == 401


def test_flow_12_agents_must_exist_and_tokens_follow_workspace_writes(live):
    admin, alice = live.http(), live.http("alice")
    unknown = admin.post("/api/tokens", json={"agent": f"ghost@{DEV}"})
    assert unknown.status_code == 404
    assert "create it with evalkit agents create" in unknown.json()["detail"]
    assert export(admin, "x", f"ghost@{DEV}").status_code == 404
    no_agent = export(admin, "x")
    assert no_agent.status_code == 400 and "X-Evalkit-Agent" in no_agent.json()["detail"]
    assert alice.post("/api/tokens", json={"agent": f"deployed@{DEV}"}).status_code == 403
    assert admin.post("/api/tokens", json={"agent": "myagent@akshat"}).status_code == 403
    assert export(alice, "x", "myagent@akshat").status_code == 403
    live.run("workspaces", "members", "add", DEV, "alice", "--role", "writer")
    mine = alice.post("/api/tokens", json={"agent": f"deployed@{DEV}"})
    assert mine.status_code == 200, mine.text
    assert admin.post("/api/tokens", json={"agent": f"deployed@{DEV}"}).status_code == 200
    assert [t["created_by"] for t in alice.get("/api/tokens").json()] == ["alice"]
    every = admin.get("/api/tokens").json()
    assert {t["created_by"] for t in every} == {"admin", "alice"}
    admins = next(t for t in every if t["created_by"] == "admin")
    assert alice.delete(f"/api/tokens/{admins['id']}").status_code in (403, 404)
    assert alice.delete(f"/api/tokens/{mine.json()['id']}").status_code == 200
    agents = {a["ref"] for a in admin.get("/api/agents").json()}
    assert f"ghost@{DEV}" not in agents


def test_flow_12_runs_browser_lists_sessions_for_otel_agents(live):
    token = live.http().post("/api/tokens", json={"agent": f"deployed@{DEV}"}).json()["token"]
    bound = bearer(live, token)
    for session_id in ("abc-1", "abc-2", "zzz-1"):
        assert export(bound, session_id).status_code == 200
    admin = live.http()
    listed = admin.get(f"/api/agents/deployed@{DEV}/runs").json()
    assert {r["run_id"] for r in listed["runs"]} == {"abc-1", "abc-2", "zzz-1"}
    prefix = admin.get(f"/api/agents/deployed@{DEV}/runs", params={"query": "abc"}).json()
    assert {r["run_id"] for r in prefix["runs"]} == {"abc-1", "abc-2"}
    none = admin.get(f"/api/agents/deployed@{DEV}/runs", params={"no_session": "true"}).json()
    assert none["runs"] == []
    doc = admin.get(f"/api/agents/deployed@{DEV}/runs/abc-1/trajectory").json()
    assert doc["extra"]["trajectory_source"] == "otel"
    assert admin.get(f"/api/agents/nope@{DEV}/runs").status_code == 404
