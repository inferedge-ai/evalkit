import time

import httpx
import pytest

from tests.api.test_otel import PROTO, otlp_payload
from tests.flows.helpers_12_17 import DEV, live_stack


@pytest.fixture
def live(db_url, tmp_path, monkeypatch, stub_judges):
    with live_stack(db_url, tmp_path, monkeypatch) as stack:
        yield stack


def lines_starting(output: str, word: str) -> list[list[str]]:
    return [line.split() for line in output.splitlines() if line.split()[:1] == [word]]


def test_flow_16_users_get_personal_workspaces_and_admin_flags(live):
    added = live.run("users", "add", "carol", input="password\n")
    assert added.exit_code == 0, added.output
    admin = live.http()
    carol_ws = admin.get("/api/workspaces/carol").json()
    assert carol_ws["type"] == "personal" and carol_ws["owner"] == "carol"
    assert live.run("users", "promote", "carol").exit_code == 0
    assert lines_starting(live.run("users", "list").output, "carol")[0][1] == "ADMIN"
    assert live.run("users", "demote", "carol").exit_code == 0
    assert lines_starting(live.run("users", "list").output, "carol")[0][1] != "ADMIN"
    taken = live.run("users", "add", "dev", input="password\n")
    assert taken.exit_code == 1
    last = live.run("users", "demote", "admin")
    assert last.exit_code == 1 and "last admin" in last.output
    assert admin.post("/api/users/admin/archive").status_code == 409


def test_flow_16_agent_tokens_outlive_the_user_who_minted_them(live, tmp_path):
    live.run("users", "add", "carol", input="password\n")
    live.run("workspaces", "members", "add", DEV, "carol", "--role", "writer")
    carol = httpx.Client(
        base_url=live.url,
        headers={
            "Authorization": "Bearer "
            + httpx.post(
                f"{live.url}/api/auth/login", json={"username": "carol", "password": "password"}
            ).json()["token"]
        },
    )
    minted = carol.post("/api/tokens", json={"agent": f"ouragent@{DEV}"})
    assert minted.status_code == 200, minted.text
    bound = httpx.Client(
        base_url=live.url, headers={"Authorization": f"Bearer {minted.json()['token']}", **PROTO}
    )
    assert bound.post("/api/otel/v1/traces", content=otlp_payload("s-1")).status_code == 200
    live.run("users", "archive", "carol")
    assert carol.get("/api/auth/me").status_code == 401
    assert bound.post("/api/otel/v1/traces", content=otlp_payload("s-2")).status_code == 200


def test_flow_16_workspaces_rename_archive_and_shared_namespace(live):
    created = live.run("workspaces", "create", "qa")
    assert created.exit_code == 0, created.output
    rows = lines_starting(live.run("workspaces", "list").output, "qa")
    assert rows[0][:4] == ["qa", "shared", "owner", "-"]
    assert any(
        row[:4] == ["akshat", "personal", "owner", "akshat"]
        for row in lines_starting(live.run("workspaces", "list").output, "akshat")
    )
    assert live.run("workspaces", "create", "akshat").exit_code == 1
    admin = live.http()
    agent = {"workspace": "qa", "name": "x", "harness_url": "http://h"}
    assert admin.post("/api/agents", json=agent).status_code == 200
    renamed = live.run("workspaces", "rename", "qa", "staging")
    assert renamed.exit_code == 0, renamed.output
    assert admin.get("/api/agents/x@staging").status_code == 200
    assert admin.get("/api/agents/x@qa").status_code == 404
    assert live.run("workspaces", "rename", "staging", "akshat").exit_code == 1
    assert live.run("workspaces", "archive", "staging").exit_code == 0
    assert not lines_starting(live.run("workspaces", "list").output, "staging")
    assert lines_starting(live.run("workspaces", "list", "--archived").output, "staging")
    assert admin.get("/api/agents/x@staging").status_code == 200
    blocked = admin.post("/api/agents", json={**agent, "workspace": "staging", "name": "y"})
    assert blocked.status_code == 409
    assert admin.get("/api/workspaces/nope").status_code == 404


def test_flow_16_members_and_what_grants_allow(live):
    assert live.run("workspaces", "members", "add", DEV, "alice", "--role", "writer").exit_code == 0
    assert (
        live.run("workspaces", "members", "add", DEV, "akshat", "--role", "reviewer").exit_code == 0
    )
    listed = live.run("workspaces", "members", "list", DEV).output
    assert any(
        line.split()[:5] == ["alice", "writer", "granted", "by", "admin"]
        for line in listed.splitlines()
    )
    members = live.http().get(f"/api/workspaces/{DEV}/members").json()
    assert all(m["granted_by"] and m["created_at"] for m in members)
    reviewers = live.http().get(f"/api/workspaces/{DEV}/members", params={"role": "reviewer"})
    assert {m["username"] for m in reviewers.json()} >= {"akshat"}
    personal = live.run("workspaces", "members", "add", "akshat", "alice", "--role", "writer")
    assert personal.exit_code == 1 and "shared workspaces only" in personal.output

    alice, akshat = live.http("alice"), live.http("akshat")
    agent = {"workspace": DEV, "name": "alices"}
    assert alice.post("/api/agents", json=agent).status_code == 200
    with_url = alice.post("/api/agents", json={**agent, "name": "urls", "harness_url": "http://h"})
    assert with_url.status_code == 200, with_url.text
    denied = akshat.post("/api/agents", json={**agent, "name": "nope"})
    assert denied.status_code == 403 and DEV in denied.json()["detail"]
    evaluator = {
        "workspace": DEV,
        "name": "alice_eval",
        "kind": "score",
        "settings": {"endpoint": "http://e"},
    }
    assert alice.post("/api/evaluators", json=evaluator).status_code == 200
    assert akshat.post("/api/evaluators", json={**evaluator, "name": "x"}).status_code == 403
    assert akshat.post("/api/workspaces", json={"name": "mine"}).status_code == 403
    assert (
        akshat.post(
            f"/api/workspaces/{DEV}/members", json={"username": "akshat", "role": "writer"}
        ).status_code
        == 403
    )

    decision = {"action": "approve"}
    path = f"/api/datasets/finance@{DEV}/cases/case-1/review"
    assert akshat.post(path, json=decision).status_code == 200
    assert alice.post(path, json=decision).status_code == 403

    admin = live.http()
    body = {
        "dataset": f"finance@{DEV}",
        "agent": f"ouragent@{DEV}",
        "selection": {"case_ids": ["case-1"]},
    }
    exp = admin.post("/api/experiments", json=body).json()
    for _ in range(400):
        if admin.get(f"/api/experiments/{exp['id']}").json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert akshat.post(f"/api/experiments/{exp['id']}/evaluate").status_code == 403
    assert alice.post(f"/api/experiments/{exp['id']}/evaluate").status_code == 200

    assert akshat.post("/api/workspaces/akshat/archive").status_code == 200


def test_flow_16_non_admins_cannot_administer(live):
    live.as_user("alice")
    assert live.run("users", "add", "zed", input="password\n").exit_code == 1
    assert live.run("workspaces", "create", "zed").exit_code == 1
    assert live.run("workspaces", "members", "add", DEV, "alice", "--role", "writer").exit_code == 1
    assert live.run("users", "list").exit_code == 1
