from datetime import UTC, datetime

import pytest

from evalkit.contracts.enums import WorkspaceRole
from evalkit.db.workspaces import WorkspaceStore
from tests.api.native_api import api_env, login_as


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        yield client, env


async def test_create_dataset(api):
    client, env = api
    store = WorkspaceStore(env.session_factory)
    await store.create_shared("prod", env.user_ids["admin"])
    alice = await login_as(client, "alice", "password")
    body = {"name": "finance", "workspace": "prod"}
    assert (await alice.post("/api/datasets", json=body)).status_code == 403
    await store.add_member("prod", "alice", WorkspaceRole.WRITER, env.user_ids["admin"])
    resp = await alice.post("/api/datasets", json=body)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ref"] == "finance@prod" and resp.json()["kind"] == "suite"
    assert (await alice.get("/api/datasets/finance@prod/cases")).json() == []
    assert (await alice.post("/api/datasets", json=body)).status_code == 409
    bad = await alice.post("/api/datasets", json={"name": "Bad Name", "workspace": "prod"})
    assert bad.status_code == 400
    mine = await alice.post("/api/datasets", json={"name": "scratch", "workspace": "alice"})
    assert mine.status_code == 200 and mine.json()["workspace_is_shared"] is False
    await store.set_archived("prod", datetime.now(UTC))
    gone = await alice.post("/api/datasets", json={"name": "other", "workspace": "prod"})
    assert gone.status_code == 409
