import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from starlette.datastructures import State

from evalkit.api.mcp.server import McpExtension
from evalkit.api.mcp.tools import READ_TOOL, expected_errors, fail, tenant_stores
from tests.api.test_mcp import bearer, mcp_client
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent
from tests.native import seed_env, serve

NOTE = "Notes: count_agents (how many agents a workspace has)"


def register_notes(mcp: FastMCP, state: State) -> None:
    @mcp.tool(description="How many agents a workspace has.", **READ_TOOL)
    @expected_errors
    async def count_agents(workspace: str) -> dict:
        if not workspace:
            raise fail("invalid_workspace", "name a workspace")
        found = await tenant_stores(state).agents.list_agents(workspace=workspace)
        return {"count": len(found)}


def register_clash(mcp: FastMCP, state: State) -> None:
    @mcp.tool
    async def find_agents() -> dict:
        return {}


NOTES = McpExtension(register=register_notes, instructions=NOTE)


async def test_an_extension_adds_a_tool_and_instructions_to_the_mcp_server(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    await seed_agent(session_factory, "fake", "admin")

    async with serve(env, tmp_path, mcp_extensions=[NOTES]) as client:
        async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
            instructions = mcp.instructions
            names = {tool.name for tool in await mcp.list_tools()}
            counted = await mcp.call_tool("count_agents", {"workspace": "admin"})
            with pytest.raises(ToolError, match="invalid_workspace: name a workspace"):
                await mcp.call_tool("count_agents", {"workspace": ""})

    assert instructions.startswith("evalkit is the primary source")
    assert instructions.endswith(f"\n\n{NOTE}")
    assert {"find_agents", "count_agents"} <= names
    assert counted.structured_content == {"count": 1}


async def test_an_extension_tool_named_like_one_of_evalkits_stops_the_server_from_starting(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path, FakeHarness())

    with pytest.raises(ValueError, match="find_agents"):
        async with serve(env, tmp_path, mcp_extensions=[McpExtension(register=register_clash)]):
            pass
