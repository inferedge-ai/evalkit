import hashlib

import httpx
import pytest

from evalkit.contracts.enums import WorkspaceRole
from evalkit.db.workspaces import WorkspaceStore
from tests.api.test_api import CASES
from tests.api.test_experiment_records import CAPS
from tests.fake_harness import FakeHarness
from tests.native import login_as, seed_env, serve, wait_for

DOC = {"schema_version": "1", "kind": {"attribute": "openinference.span.kind"}}
FINANCE = "finance@canonical"
SHARED = {"dataset": FINANCE, "agent": "fake@canonical"}
PERSONAL = {
    "dataset": FINANCE,
    "agent": "fake@akshat",
    "capabilities": CAPS,
    "holder": "cli:akshat@laptop:1",
}


async def _login(admin: httpx.AsyncClient, username: str) -> httpx.AsyncClient:
    client = httpx.AsyncClient(transport=admin._transport, base_url="http://test")
    resp = await client.post("/api/auth/login", json={"username": username, "password": "password"})
    client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    return client


@pytest.fixture
async def users(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    async with serve(env, tmp_path) as admin:
        for name in ("bob", "carol"):
            await admin.post("/api/users", json={"username": name, "password": "password"})
        await WorkspaceStore(session_factory).add_member(
            "canonical", "bob", WorkspaceRole.WRITER, granted_by=env.user_ids["admin"]
        )
        clients = {"admin": admin, "akshat": await login_as(admin, "akshat")}
        for name in ("bob", "carol"):
            clients[name] = await _login(admin, name)
        yield clients


def sync(client, workspace: str | None = None, **params):
    query = {"dataset": "perm", **params}
    if workspace:
        query["workspace"] = workspace
    return client.post("/api/sync", params=query, files=[("files", ("c.yaml", CASES))])


async def test_sync_needs_write_on_the_target(users):
    assert (await sync(users["bob"], "canonical")).status_code == 200
    assert (await sync(users["carol"], "canonical")).status_code == 403
    assert (await sync(users["akshat"])).status_code == 200
    assert (await sync(users["akshat"], "bob")).status_code == 403
    assert (await sync(users["bob"], "canonical", actor="x@y")).status_code == 403


async def test_any_user_may_upload_fixture_bytes(users):
    digest = hashlib.sha256(b"a").hexdigest()
    for name in ("bob", "carol"):
        ok = await users[name].put(f"/api/fixtures/sha256/{digest}", content=b"a")
        assert ok.status_code == 200


async def test_create_experiment_follows_can_use_agent_and_membership(users):
    refused = await users["carol"].post("/api/experiments", json=SHARED)
    assert refused.status_code == 403
    assert "only its writers, reviewers and admins" in refused.json()["detail"]
    shared = await users["bob"].post("/api/experiments", json=SHARED)
    assert shared.status_code == 200
    assert (await users["carol"].post("/api/experiments", json=PERSONAL)).status_code == 403
    assert (await users["akshat"].post("/api/experiments", json=PERSONAL)).status_code == 200
    await wait_for(users["bob"], shared.json()["id"])


async def test_experiment_changes_need_write_or_creator(users):
    mine = (await users["akshat"].post("/api/experiments", json=PERSONAL)).json()["id"]
    patch = {"model": "m"}
    assert (await users["bob"].patch(f"/api/experiments/{mine}", json=patch)).status_code == 403
    denied = await users["admin"].patch(f"/api/experiments/{mine}", json=patch)
    assert denied.status_code == 403
    assert (await users["akshat"].patch(f"/api/experiments/{mine}", json=patch)).status_code == 200
    theirs = (await users["bob"].post("/api/experiments", json=SHARED)).json()["id"]
    await wait_for(users["bob"], theirs)
    server_only = await users["bob"].patch(f"/api/experiments/{theirs}", json=patch)
    assert server_only.status_code == 409 and "driven by the server" in server_only.json()["detail"]
    denied = await users["carol"].patch(f"/api/experiments/{theirs}", json=patch)
    assert denied.status_code == 403


async def test_server_resume_and_evaluate_only_for_server_driven_experiments(users):
    mine = (await users["akshat"].post("/api/experiments", json=PERSONAL)).json()["id"]
    resumed = await users["akshat"].post(f"/api/experiments/{mine}/resume")
    assert resumed.status_code == 409
    assert f"evalkit run {FINANCE} --agent fake@akshat --resume {mine}" in resumed.json()["detail"]
    evaluated = await users["akshat"].post(f"/api/experiments/{mine}/evaluate")
    assert evaluated.status_code == 409 and "evalkit evaluate" in evaluated.json()["detail"]
    theirs = (await users["bob"].post("/api/experiments", json=SHARED)).json()["id"]
    await wait_for(users["bob"], theirs)
    assert (await users["akshat"].post(f"/api/experiments/{theirs}/evaluate")).status_code == 403
    assert (await users["bob"].post(f"/api/experiments/{theirs}/evaluate")).status_code == 200
    await wait_for(users["bob"], theirs)


async def test_mapping_writes_need_write_on_the_agents_workspace(users):
    url = "/api/agents/fake@canonical/mappings/draft"
    assert (await users["bob"].put(url, json=DOC)).status_code == 200
    assert (await users["carol"].put(url, json=DOC)).status_code == 403


async def test_harness_check_follows_can_use_agent(users):
    assert (await users["carol"].post("/api/agents/fake@akshat/check")).status_code == 403
    own = await users["akshat"].post("/api/agents/fake@akshat/check")
    assert own.status_code == 400 and "does not drive" in own.json()["detail"]
    assert (await users["carol"].post("/api/agents/fake@canonical/check")).status_code == 200


async def test_archived_agent_refuses_check_token_and_local_resume(users):
    mine = (await users["akshat"].post("/api/experiments", json=PERSONAL)).json()["id"]
    assert (await users["admin"].post("/api/agents/fake@canonical/archive")).status_code == 200
    assert (await users["akshat"].post("/api/agents/fake@akshat/archive")).status_code == 200
    check = await users["carol"].post("/api/agents/fake@canonical/check")
    assert check.status_code == 409 and "archived" in check.json()["detail"]
    token = await users["akshat"].post("/api/tokens", json={"agent": "fake@akshat"})
    assert token.status_code == 409 and "archived" in token.json()["detail"]
    resume = await users["akshat"].patch(
        f"/api/experiments/{mine}",
        json={"status": "evaluating", "holder": PERSONAL["holder"]},
    )
    assert resume.status_code == 409 and "archived" in resume.json()["detail"]
