import httpx
import pytest
from sqlalchemy import select

from evalkit.contracts.enums import WorkspaceRole
from evalkit.db.models import AuthToken
from evalkit.db.workspaces import WorkspaceStore
from tests.api.native_api import api_env, login_as
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        for name, workspace in (("sdk", "admin"), ("x", "admin"), ("x", "alice")):
            await seed_agent(session_factory, name, workspace, harness_url=None)
        yield client


def bearer(client: httpx.AsyncClient, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=client._transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_mint_list_use_and_revoke(api):
    minted = await api.post("/api/tokens", json={"agent": "sdk@admin"})
    assert minted.status_code == 200, minted.text
    body = minted.json()
    assert body["agent"] == "sdk@admin" and body["token"]
    agents = {a["slug"] for a in (await api.get("/api/agents")).json()}
    assert "sdk@admin" in agents
    listed = (await api.get("/api/tokens")).json()
    assert [t["agent"] for t in listed] == ["sdk@admin"] and listed[0]["created_by"] == "admin"
    other = bearer(api, body["token"])
    assert (await other.get("/api/datasets")).status_code == 403
    assert (await other.get("/api/auth/me")).status_code == 403
    assert (await api.delete(f"/api/tokens/{body['id']}")).json() == {"ok": True}
    assert (await other.get("/api/datasets")).status_code == 401
    await other.aclose()


async def test_minting_needs_write_on_the_agents_workspace(api, session_factory):
    alice = await login_as(api, "alice", "password")
    assert (await alice.post("/api/tokens", json={"agent": "fake@canonical"})).status_code == 403
    assert (await alice.post("/api/tokens", json={"agent": "x@admin"})).status_code == 403
    assert (await alice.post("/api/tokens", json={"agent": "x"})).status_code == 200
    mine = (await alice.get("/api/tokens")).json()
    assert [t["agent"] for t in mine] == ["x@alice"]
    assert (await api.post("/api/tokens", json={"agent": "fake"})).status_code == 200
    unknown = await api.post("/api/tokens", json={"agent": "ghost"})
    assert unknown.status_code == 404
    assert "create it with evalkit agents create" in unknown.json()["detail"]
    await WorkspaceStore(session_factory).add_member(
        "canonical", "alice", WorkspaceRole.WRITER, granted_by=1
    )
    assert (await alice.post("/api/tokens", json={"agent": "fake@canonical"})).status_code == 200
    await alice.aclose()


async def test_agent_token_row_has_kind_agent(api, session_factory):
    await api.post("/api/tokens", json={"agent": "sdk@admin"})
    async with session_factory() as session:
        kinds = (
            await session.scalars(select(AuthToken.kind).where(AuthToken.agent_id.isnot(None)))
        ).all()
    assert kinds == ["agent"]
