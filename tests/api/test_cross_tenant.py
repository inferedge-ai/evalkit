from datetime import timedelta

import httpx

from evalkit.api.auth import issue_token
from evalkit.contracts.enums import WorkspaceRole
from evalkit.core.runner import ExperimentRequest
from evalkit.db.models import Tenant
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.api.native_api import api_env
from tests.native import start


async def _second_tenant(client, raw_session_factory) -> httpx.AsyncClient:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    root = await UserStore(raw_session_factory, 2).create("root", "password", is_admin=True)
    await WorkspaceStore(raw_session_factory, 2).create_shared("canonical", root.id)
    other = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    async with tenant_session(raw_session_factory, 2) as session:
        token = await issue_token(session, root, timedelta(hours=1))
    other.headers["Authorization"] = f"Bearer {token}"
    return other


async def test_an_experiment_by_id_is_404_from_another_tenant(
    tmp_path, session_factory, raw_session_factory
):
    async with api_env(tmp_path, session_factory) as (client, env):
        exp = await start(
            env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical")
        )
        assert (await client.get(f"/api/experiments/{exp.id}")).status_code == 200
        other = await _second_tenant(client, raw_session_factory)
        resp = await other.get(f"/api/experiments/{exp.id}")
        assert resp.status_code == 404
        resp = await other.get(f"/api/experiments/{exp.id}/runs")
        assert resp.status_code == 404


async def test_a_workspace_by_name_resolves_inside_the_callers_tenant(
    tmp_path, session_factory, raw_session_factory
):
    async with api_env(tmp_path, session_factory) as (client, env):
        other = await _second_tenant(client, raw_session_factory)
        ours = (await client.get("/api/workspaces/canonical")).json()
        theirs = (await other.get("/api/workspaces/canonical")).json()
        assert ours["id"] != theirs["id"]
        assert (ours["tenant_id"], theirs["tenant_id"]) == (1, 2)
        assert (await client.get("/api/datasets/finance@canonical/cases")).status_code == 200
        assert (await other.get("/api/datasets/finance@canonical/cases")).status_code == 404
        assert (await other.get("/api/workspaces/akshat")).status_code == 404


async def test_a_member_grant_cannot_cross_tenants(tmp_path, session_factory, raw_session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        other = await _second_tenant(client, raw_session_factory)
        resp = await other.post(
            "/api/workspaces/canonical/members",
            json={"username": "alice", "role": WorkspaceRole.WRITER.value},
        )
        assert resp.status_code == 404
