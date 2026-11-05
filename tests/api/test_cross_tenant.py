from datetime import timedelta

import httpx2

from evalkit.api.auth import issue_token
from evalkit.contracts.enums import TrajectorySource, WorkspaceRole
from evalkit.core.runner import ExperimentRequest
from evalkit.db.models import TenantSettings
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.api.native_api import api_env
from tests.api.test_mcp import call
from tests.api.test_otel import PROTO, otlp_payload, reference_mapping
from tests.identity import seed_agent
from tests.native import start


async def _second_tenant(client, raw_session_factory) -> httpx2.AsyncClient:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(TenantSettings(tenant_id="tenant-2"))
        await s.commit()
    root = await UserStore(raw_session_factory, "tenant-2").create(
        "root", password="password", is_admin=True
    )
    await WorkspaceStore(raw_session_factory, "tenant-2").create_shared("canonical", root.id)
    other = httpx2.AsyncClient(transport=client._transport, base_url="http://test")
    async with tenant_session(raw_session_factory, "tenant-2") as session:
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
        assert (await client.get(f"/api/experiments/{exp.id}/runs")).status_code == 200
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
        assert (ours["tenant_id"], theirs["tenant_id"]) == ("default", "tenant-2")
        assert (await client.get("/api/datasets/finance@canonical/cases")).status_code == 200
        assert (await other.get("/api/datasets/finance@canonical/cases")).status_code == 404
        assert (await other.get("/api/workspaces/akshat")).status_code == 404


async def test_a_member_grant_cannot_cross_tenants(tmp_path, session_factory, raw_session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        grant = {"username": "alice", "role": WorkspaceRole.WRITER.value}
        assert (
            await client.post("/api/workspaces/canonical/members", json=grant)
        ).status_code == 200
        other = await _second_tenant(client, raw_session_factory)
        resp = await other.post("/api/workspaces/canonical/members", json=grant)
        assert resp.status_code == 404


async def test_a_comment_by_id_cannot_be_resolved_or_replied_to_from_another_tenant(
    tmp_path, session_factory, raw_session_factory
):
    async with api_env(tmp_path, session_factory) as (client, env):
        cases = (await client.get("/api/datasets/finance@canonical/cases")).json()
        case_id = cases[0]["case_id"]
        base = f"/api/datasets/finance@canonical/cases/{case_id}/comments"
        posted = await client.post(base, json={"body": "why?"})
        assert posted.status_code == 200, posted.text
        comment_id = posted.json()["id"]
        other = await _second_tenant(client, raw_session_factory)
        resolved = await other.patch(f"{base}/{comment_id}", json={"resolved": True})
        assert resolved.status_code == 404
        reply = await other.post(base, json={"body": "x", "parent_id": comment_id})
        assert reply.status_code in (400, 404)
        threads = (await client.get(base)).json()["threads"]
        assert [(t["id"], t["resolved"]) for t in threads] == [(comment_id, False)]


async def test_evaluations_for_another_tenants_run_do_not_leak_the_experiment(
    tmp_path, session_factory, raw_session_factory
):
    async with api_env(tmp_path, session_factory) as (client, env):
        exp = await start(
            env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical")
        )
        runs = await client.get(f"/api/experiments/{exp.id}/runs")
        run_id = runs.json()[0]["experiment_run_id"]
        other = await _second_tenant(client, raw_session_factory)
        resp = await other.post(
            f"/api/experiment-runs/{run_id}/evaluations",
            json={"rows": [], "holder": "cli:x@host:1"},
        )
        assert resp.status_code == 404
        detail = resp.json()["detail"]
        assert detail == f"unknown experiment run: {run_id}"


async def test_another_tenants_admin_cannot_revoke_an_agent_token(
    tmp_path, session_factory, raw_session_factory
):
    async with api_env(tmp_path, session_factory) as (client, env):
        created = await client.post("/api/tokens", json={"agent": "fake@canonical"})
        assert created.status_code == 200, created.text
        token_id = created.json()["id"]
        other = await _second_tenant(client, raw_session_factory)
        assert (await other.delete(f"/api/tokens/{token_id}")).status_code == 404
        assert token_id in [t["id"] for t in (await client.get("/api/tokens")).json()]
        assert (await other.get("/api/tokens")).json() == []


async def test_list_endpoints_show_only_the_callers_tenant(
    tmp_path, session_factory, raw_session_factory
):
    async with api_env(tmp_path, session_factory) as (client, env):
        await client.post(
            "/api/llm-providers", json={"name": "p", "base_url": "https://llm.example"}
        )
        await client.post(
            "/api/trace-sources",
            json={"name": "phx", "provider": "phoenix", "url": "https://p", "api_key": "k"},
        )
        other = await _second_tenant(client, raw_session_factory)
        for path in ("/api/agents", "/api/evaluators", "/api/llm-providers", "/api/trace-sources"):
            ours = (await client.get(path)).json()
            theirs = (await other.get(path)).json()
            assert ours, path
            assert theirs == [], path


async def test_mcp_tools_see_only_the_callers_tenant(
    tmp_path, session_factory, raw_session_factory
):
    async with api_env(tmp_path, session_factory) as (client, env):
        other = await _second_tenant(client, raw_session_factory)
        other.app = client.app  # type: ignore[attr-defined]
        ours = await call(client, "find_agents")
        theirs = await call(other, "find_agents")
    assert ours["meta"]["count"] > 0
    assert theirs["meta"]["count"] == 0


async def test_a_second_tenants_agent_token_exports_only_to_its_own_agent(
    tmp_path, session_factory, raw_session_factory
):
    async with api_env(tmp_path, session_factory) as (client, env):
        other = await _second_tenant(client, raw_session_factory)
        await seed_agent(
            raw_session_factory,
            "traced",
            "canonical",
            harness_url=None,
            trajectory_source=TrajectorySource.OTEL,
            mapping=reference_mapping(),
            tenant_id="tenant-2",
        )
        token = (await other.post("/api/tokens", json={"agent": "traced@canonical"})).json()
        headers = {**PROTO, "Authorization": f"Bearer {token['token']}"}
        resp = await client.post("/api/otel/v1/traces", content=otlp_payload(), headers=headers)
        assert resp.status_code == 200, resp.text
        theirs = await other.get("/api/agents/traced@canonical/sessions/sess-1/trajectory")
        ours = await client.get("/api/agents/traced@canonical/sessions/sess-1/trajectory")
    assert theirs.status_code == 200 and theirs.json()["span_count"] == 2
    assert ours.status_code == 404
