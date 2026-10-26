import pytest

from tests.api.native_api import api_env, login_as
from tests.fake_harness import FakeHarness

BODY = {
    "workspace": "canonical",
    "name": "compliance",
    "kind": "score",
    "settings": {"endpoint": "http://e"},
}


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        yield client


async def test_evaluator_crud_follows_workspace_write(api):
    alice = await login_as(api, "alice", "password")
    assert (await alice.post("/api/evaluators", json=BODY)).status_code == 403
    created = await api.post("/api/evaluators", json=BODY)
    assert created.status_code == 200 and created.json()["ref"] == "compliance@canonical"
    assert (await api.post("/api/evaluators", json=BODY)).status_code == 409
    own = await alice.post("/api/evaluators", json={**BODY, "workspace": "alice"})
    assert own.status_code == 200
    listed = (await alice.get("/api/evaluators", params={"workspace": "canonical"})).json()
    names = {e["name"] for e in listed}
    assert "compliance" in names
    patched = await api.patch(
        "/api/evaluators/compliance@canonical", json={"settings": {"endpoint": "http://f"}}
    )
    assert patched.json()["settings"]["endpoint"] == "http://f"
    denied = await alice.post("/api/evaluators/compliance@canonical/archive")
    assert denied.status_code == 403
    assert (await api.post("/api/evaluators/compliance@canonical/archive")).status_code == 200
    await alice.aclose()


async def test_only_admins_set_shared_evaluator_endpoints(api):
    await api.post("/api/users", json={"username": "bob", "password": "password"})
    await api.post("/api/workspaces", json={"name": "dev"})
    await api.post("/api/workspaces/dev/members", json={"username": "bob", "role": "writer"})
    bob = await login_as(api, "bob", "password")
    shared = {**BODY, "workspace": "dev"}
    refused = await bob.post("/api/evaluators", json=shared)
    assert refused.status_code == 403 and "only an admin" in refused.json()["detail"]
    assert (await api.post("/api/evaluators", json=shared)).status_code == 200
    moved = await bob.patch(
        "/api/evaluators/compliance@dev", json={"settings": {"endpoint": "http://evil"}}
    )
    assert moved.status_code == 403
    timeout = await bob.patch(
        "/api/evaluators/compliance@dev",
        json={"settings": {"endpoint": "http://e", "timeout_seconds": 30}},
    )
    assert timeout.status_code == 200 and timeout.json()["settings"]["timeout_seconds"] == 30
    await bob.aclose()
