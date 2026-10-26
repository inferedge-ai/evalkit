import pytest

from tests.api.native_api import api_env, login_as
from tests.fake_harness import FakeHarness


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        yield client


async def _user(client, name):
    resp = await client.post("/api/users", json={"username": name, "password": "password"})
    assert resp.status_code == 200, resp.text
    return await login_as(client, name, "password")


async def test_create_user_makes_personal_workspace(api):
    client = api
    bob = await _user(client, "bob")
    ws = (await client.get("/api/workspaces/bob")).json()
    assert (
        await bob.post("/api/users", json={"username": "x", "password": "y"})
    ).status_code == 403
    assert (await bob.post("/api/users/alice/archive")).status_code == 403
    assert ws["type"] == "personal" and not ws["archived"]
    assert (await client.get("/api/workspaces/ghost")).status_code == 404


async def test_admin_manages_shared_workspace_and_members(api):
    client = api
    bob = await _user(client, "bob")
    resp = await client.post("/api/workspaces", json={"name": "dev"})
    assert resp.status_code == 200 and resp.json()["type"] == "shared"
    assert (await bob.post("/api/workspaces", json={"name": "qa"})).status_code == 403
    assert (await client.post("/api/workspaces", json={"name": "bob"})).status_code == 409
    resp = await client.post(
        "/api/workspaces/dev/members", json={"username": "bob", "role": "writer"}
    )
    assert resp.status_code == 200
    resp = await client.post(
        "/api/workspaces/bob/members", json={"username": "alice", "role": "writer"}
    )
    assert resp.status_code == 400
    renamed = await client.patch("/api/workspaces/dev", json={"name": "staging"})
    assert renamed.status_code == 200 and renamed.json()["name"] == "staging"
    assert any("@dev to @staging" in line for line in renamed.json()["update"])
    personal = await client.patch("/api/workspaces/bob", json={"name": "robert"})
    assert personal.status_code == 400 and "owner's name" in personal.json()["detail"]
    bad = await client.patch("/api/workspaces/staging", json={"name": "Bad Name"})
    assert bad.status_code == 400 and "invalid name" in bad.json()["detail"]
    members = (await bob.get("/api/workspaces/staging/members")).json()
    assert [(m["username"], m["role"], m["granted_by"]) for m in members] == [
        ("bob", "writer", "admin")
    ]
    assert (await bob.get("/api/auth/me")).json()["roles"] == {"staging": ["writer"]}
    resp = await client.delete("/api/workspaces/staging/members/bob/writer")
    assert resp.status_code == 200
    assert (await bob.get("/api/workspaces/staging/members")).json() == []


async def test_archive_rules(api):
    client = api
    bob = await _user(client, "bob")
    await client.post("/api/workspaces", json={"name": "dev"})
    assert (await bob.post("/api/workspaces/dev/archive")).status_code == 403
    assert (await bob.post("/api/workspaces/bob/archive")).status_code == 200
    assert (await client.post("/api/workspaces/alice/archive")).status_code == 403
    assert (await client.post("/api/workspaces/dev/archive")).status_code == 200
    names = [w["name"] for w in (await bob.get("/api/workspaces")).json()]
    assert "dev" not in names and "bob" not in names


async def test_only_workspace_reviewers_decide(api):
    client = api
    bob = await _user(client, "bob")
    carol = await _user(client, "carol")
    await client.post(
        "/api/workspaces/canonical/members", json={"username": "bob", "role": "reviewer"}
    )
    url = "/api/datasets/finance@canonical/cases/case-1/review"
    assert (await carol.post(url, json={"action": "approve"})).status_code == 403
    assert (await bob.post(url, json={"action": "approve"})).status_code == 200
    resp = await carol.post(
        "/api/datasets/finance@canonical/review-requests",
        json={"selection": {"all": True}, "reviewer": "carol"},
    )
    assert resp.status_code == 400 and "not a reviewer of canonical" in resp.json()["detail"]


async def test_promotion_link(api):
    client = api
    bob = await _user(client, "bob")
    for name in ("dev", "prod", "qa"):
        await client.post("/api/workspaces", json={"name": name})
    assert (await bob.patch("/api/workspaces/dev", json={"promotes_to": "prod"})).status_code == 403
    resp = await client.patch("/api/workspaces/dev", json={"promotes_to": "prod"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["promotes_to"] == "prod" and resp.json()["promoted_from"] is None
    assert any("sync into @dev instead" in line for line in resp.json()["update"])
    prod = (await client.get("/api/workspaces/prod")).json()
    assert prod["promoted_from"] == "dev" and prod["promotes_to"] is None
    listed = {ws["name"]: ws for ws in (await bob.get("/api/workspaces")).json()}
    assert listed["dev"]["promotes_to"] == "prod" and listed["prod"]["promoted_from"] == "dev"
    assert (
        await client.patch("/api/workspaces/qa", json={"promotes_to": "prod"})
    ).status_code == 409
    renamed = await client.patch("/api/workspaces/prod", json={"name": "live"})
    assert renamed.status_code == 200 and renamed.json()["promoted_from"] == "dev"
    assert (await client.get("/api/workspaces/dev")).json()["promotes_to"] == "live"
    cleared = await client.patch("/api/workspaces/dev", json={"promotes_to": None})
    assert cleared.status_code == 200 and cleared.json()["promotes_to"] is None
    assert (await client.get("/api/workspaces/live")).json()["promoted_from"] is None
