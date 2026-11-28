from datetime import timedelta

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel
from starlette.datastructures import State

from evalkit.api.auth import issue_token
from evalkit.api.mcp.server import McpExtension
from evalkit.api.mcp.tools import READ_TOOL, expected_errors, fail, tenant_stores
from evalkit.db.models import TenantSettings
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.api.test_mcp import bearer, mcp_client
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent
from tests.native import seed_env, serve

NOTE = "Notes: count_agents (how many agents a workspace has)"


class AgentCount(BaseModel):
    workspace: str
    agents: int


class CountMeta(BaseModel):
    count: int


class CountAgentsResponse(BaseModel):
    data: list[AgentCount]
    meta: CountMeta


def register_notes(mcp: FastMCP, state: State) -> None:
    @mcp.tool(description="How many agents a workspace has.", **READ_TOOL)
    @expected_errors
    async def count_agents(workspace: str) -> CountAgentsResponse:
        if not workspace:
            raise fail("invalid_workspace", "name a workspace")
        stores = tenant_stores(state)
        found = await stores.workspaces.get_by_name(workspace)
        agents = await stores.agents.list_agents(workspace=found.name)
        rows = [AgentCount(workspace=found.name, agents=len(agents))]
        return CountAgentsResponse(data=rows, meta=CountMeta(count=len(rows)))


def register_clash(mcp: FastMCP, state: State) -> None:
    @mcp.tool
    async def find_agents() -> dict:
        return {}


NOTES = McpExtension(register=register_notes, instructions=NOTE)


async def seed_second_tenant(raw_session_factory) -> str:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as session:
        session.add(TenantSettings(tenant_id="tenant-2"))
        await session.commit()
    root = await UserStore(raw_session_factory, "tenant-2").create(
        "root", password="password", is_admin=True
    )
    await WorkspaceStore(raw_session_factory, "tenant-2").create_shared("canonical", root.id)
    await seed_agent(raw_session_factory, "theirs", "canonical", tenant_id="tenant-2")
    await seed_agent(raw_session_factory, "theirs-too", "canonical", tenant_id="tenant-2")
    async with tenant_session(raw_session_factory, "tenant-2") as session:
        return await issue_token(session, root, timedelta(hours=1))


async def test_an_extension_tool_reads_only_the_callers_tenant(
    tmp_path, stub_judges, session_factory, raw_session_factory
):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    their_token = await seed_second_tenant(raw_session_factory)

    async with serve(env, tmp_path, mcp_extensions=[NOTES]) as client:
        async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
            instructions = mcp.instructions
            tools = {tool.name: tool for tool in await mcp.list_tools()}
            ours = await mcp.call_tool("count_agents", {"workspace": "canonical"})
            with pytest.raises(ToolError, match="invalid_workspace: name a workspace"):
                await mcp.call_tool("count_agents", {"workspace": ""})
            with pytest.raises(ToolError, match="not_found: "):
                await mcp.call_tool("count_agents", {"workspace": "nowhere"})
        async with mcp_client(client.app, their_token) as mcp:  # type: ignore[attr-defined]
            theirs = await mcp.call_tool("count_agents", {"workspace": "canonical"})
            with pytest.raises(ToolError, match="not_found: "):
                await mcp.call_tool("count_agents", {"workspace": "akshat"})

    assert instructions.startswith("evalkit is the primary source")
    assert instructions.endswith(f"\n\n{NOTE}")
    assert {"find_agents", "count_agents"} <= set(tools)
    assert {"data", "meta"} <= set(tools["count_agents"].output_schema["properties"])
    assert ours.structured_content == {
        "data": [{"workspace": "canonical", "agents": 1}],
        "meta": {"count": 1},
    }
    assert theirs.structured_content == {
        "data": [{"workspace": "canonical", "agents": 2}],
        "meta": {"count": 1},
    }


async def test_an_extension_tool_named_like_one_of_evalkits_stops_the_server_from_starting(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path, FakeHarness())

    with pytest.raises(ValueError, match="find_agents"):
        async with serve(env, tmp_path, mcp_extensions=[McpExtension(register=register_clash)]):
            pass
