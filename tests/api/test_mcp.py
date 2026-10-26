import asyncio
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import httpx2
import pytest
from fastapi import HTTPException
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

from evalkit.api import runs as runs_api
from evalkit.api.app import create_app
from evalkit.api.mcp import descriptions as mcp_descriptions
from evalkit.api.mcp import tools as mcp_tools
from evalkit.config import McpConfig, McpOAuthConfig
from tests.api.native_api import api_env
from tests.fake_harness import FakeHarness
from tests.mcp_trajectory import chat_session
from tests.native import HOLDER, USERS, Env, seed_env

ISSUER = "https://id.example.test"
RESOURCE = "http://evalkit.test/mcp"
METADATA = "http://evalkit.test/.well-known/oauth-protected-resource/mcp"
DAY_ONE = datetime(2026, 10, 20, 23, 0, tzinfo=UTC)
DAY_TWO = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
MCP_ACCEPT = "application/json, text/event-stream"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}


def fake_with_runs() -> FakeHarness:
    fake = FakeHarness(
        trajectory_doc=chat_session(), workspace_files={"outputs/r.md": b"# report\nbody"}
    )
    for run_id, created in (("s-1", DAY_ONE), ("s-2", DAY_TWO), ("s-3", DAY_TWO)):
        fake.runs[run_id] = {"polls": 0, "stopped": False, "payload": {}, "created_at": created}
    return fake


def mcp_client(app, token: str | None) -> Client:
    def factory(**kwargs) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://test", **kwargs
        )

    return Client(
        StreamableHttpTransport("http://test/mcp", auth=token, httpx_client_factory=factory)
    )


def bearer(client: httpx.AsyncClient) -> str:
    return client.headers["Authorization"].removeprefix("Bearer ")


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, fake_with_runs()) as (client, env):
        yield client, env


async def call(client: httpx.AsyncClient, tool: str, **args) -> dict:
    async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
        result = await mcp.call_tool(tool, args)
    return result.structured_content or json.loads(result.content[0].text)


async def test_mcp_refuses_requests_without_a_token(api):
    client, _ = api
    resp = await client.post(
        "/mcp", json={}, headers={"Authorization": "", "Accept": "application/json"}
    )
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Bearer")


async def mcp_status(http: httpx.AsyncClient, token: str) -> int:
    resp = await http.post(
        "/mcp",
        json=INITIALIZE,
        headers={"Authorization": f"Bearer {token}", "Accept": MCP_ACCEPT},
    )
    return resp.status_code


async def test_agent_tokens_cannot_use_mcp(api):
    client, _ = api
    token = (await client.post("/api/tokens", json={"agent": "fake@admin"})).json()["token"]
    assert await mcp_status(client, token) == 401
    assert await mcp_status(client, bearer(client)) == 200


async def test_mcp_requests_are_held_to_the_request_body_limit(api):
    client, _ = api
    client.app.state.cfg.limits.request_bytes = 64  # type: ignore[attr-defined]
    resp = await client.post(
        "/mcp",
        json={**INITIALIZE, "padding": "x" * 100},
        headers={"Authorization": f"Bearer {bearer(client)}", "Accept": MCP_ACCEPT},
    )
    assert resp.status_code == 413


async def test_mcp_refuses_a_login_token_api_refuses(api):
    client, _ = api
    login = await client.post(
        "/api/auth/login", json={"username": "alice", "password": USERS["alice"]}
    )
    token = login.json()["token"]
    assert await mcp_status(client, token) == 200
    assert (await client.post("/api/users/alice/archive")).status_code == 200
    gone = await client.get("/api/agents", headers={"Authorization": f"Bearer {token}"})
    assert gone.status_code == 401
    assert await mcp_status(client, token) == 401


async def test_download_tickets_cannot_use_mcp(api):
    client, _ = api
    ticket = (await client.post("/api/auth/download-ticket")).json()["ticket"]
    assert await mcp_status(client, ticket) == 401


TOOLS = {
    "find_agents": False,
    "find_runs": True,
    "get_runs": True,
    "get_run_stats": True,
    "get_trajectory": True,
    "get_run_files": True,
    "download_run_file": True,
    "find_experiments": False,
    "get_experiments": False,
    "get_experiment_runs": False,
}
MONEY_KEYS = {"value", "unit", "currency"}
CHAINED = {"get_run_files", "download_run_file", "get_experiments"}


async def test_tool_surface_follows_the_shared_contract(api):
    client, _ = api
    async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
        tools = await mcp.list_tools()
    assert {t.name: t.annotations.open_world_hint for t in tools} == TOOLS
    for tool in tools:
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert (tool.meta or {}).get("anthropic/alwaysLoad") is True, tool.name
        schema = tool.output_schema
        assert schema and {"data", "meta"} <= set(schema["properties"]), tool.name
        assert schema["properties"]["data"]["type"] == "array", tool.name
        assert "Returns:" in tool.description and "Args:" in tool.description, tool.name
        if tool.name not in CHAINED:
            assert "('" in tool.description[:100], tool.name


async def test_find_agents_and_runs(api):
    client, _ = api
    agents = await call(client, "find_agents", workspace="canonical")
    assert [a["id"] for a in agents["data"]] == ["fake@canonical"]
    assert agents["meta"] == {"count": 1}
    runs = await call(client, "find_runs", agent="fake@canonical", limit=2)
    assert [r["id"] for r in runs["data"]] == ["s-2", "s-3"]
    assert runs["data"][0]["user_id"] == "dummy" and runs["data"][0]["source"] == "app"
    assert "metrics" not in runs["data"][0] or runs["data"][0]["metrics"] is None
    assert runs["meta"]["count"] == 2 and runs["meta"]["total"] == 3
    assert runs["meta"]["has_more"] is True and runs["meta"]["next_offset"] == 2

    window = await call(
        client,
        "find_runs",
        agent="fake@canonical",
        start_date="2026-10-21",
        end_date="2026-10-21",
        include=["metrics"],
    )
    assert sorted(r["id"] for r in window["data"]) == ["s-2", "s-3"]
    metrics = window["data"][0]["metrics"]
    assert (metrics["turns"], metrics["failed_turns"], metrics["duration_seconds"]) == (2, 1, 130.0)
    assert set(metrics["cost"]) == MONEY_KEYS and metrics["cost"]["currency"] == "USD"
    assert metrics["cost_source"] == "mixed"
    assert metrics["tool_errors_by_tool"][0] == {
        "tool": "search_docs",
        "count": 2,
        "message": "unknown_column: name",
    }


async def test_find_runs_by_tool_error_lists_the_runs_it_errored_in(api):
    client, _ = api
    found = await call(
        client, "find_runs", agent="fake@canonical", tool_error="search_docs", limit=2
    )
    assert found["meta"]["total"] == 3 and found["meta"]["has_more"] is True
    assert len(found["data"]) == 2
    for row in found["data"]:
        (entry,) = [e for e in row["metrics"]["tool_errors_by_tool"] if e["tool"] == "search_docs"]
        assert entry["count"] == 2 and entry["message"]
    none = await call(client, "find_runs", agent="fake@canonical", tool_error="no_such_tool")
    assert none["data"] == [] and none["meta"]["total"] == 0


async def test_runs_include_their_tool_calls_grouped_with_arguments(api):
    client, _ = api
    found = await call(client, "find_runs", agent="fake@canonical", include=["tool_calls"])
    (row,) = [r for r in found["data"] if r["id"] == "s-1"]
    assert "metrics" not in row and row["tool_calls"]
    assert all(set(g) == {"tool", "count", "arguments"} for g in row["tool_calls"])
    counts = [g["count"] for g in row["tool_calls"]]
    assert counts == sorted(counts, reverse=True)
    assert {g["tool"] for g in row["tool_calls"]} >= {"search_docs", "Bash"}


async def test_find_runs_failed_only_lists_runs_with_a_failure(api):
    client, _ = api
    found = await call(
        client, "find_runs", agent="fake@canonical", failed_only=True, include=["turns"]
    )
    assert found["meta"]["total"] == 3
    for row in found["data"]:
        assert row["metrics"]["failures"] == 1
        assert [t["status"] for t in row["turns"]] == ["ok", "failed"]
    none = await call(
        client, "find_runs", agent="fake@canonical", failed_only=True, tool_error="no_such_tool"
    )
    assert none["meta"]["total"] == 0


async def test_find_runs_with_include_takes_a_full_page(api):
    client, _ = api
    found = await call(client, "find_runs", agent="fake@canonical", include=["metrics"], limit=200)
    assert len(found["data"]) == 3


async def test_an_unreachable_trajectory_fails_its_row_not_the_call(api, monkeypatch):
    client, _ = api
    read = runs_api.read_trajectory

    async def flaky(record, run_id, *args):
        if run_id == "s-2":
            raise httpx.ConnectError("harness went away")
        return await read(record, run_id, *args)

    monkeypatch.setattr(runs_api, "read_trajectory", flaky)
    found = await call(client, "find_runs", agent="fake@canonical", include=["metrics"])
    by_id = {row["id"]: row for row in found["data"]}
    assert "metrics" not in by_id["s-2"]
    assert by_id["s-2"]["error"] == "harness_failed"
    assert "harness unreachable" in by_id["s-2"]["detail"]
    assert by_id["s-1"]["metrics"] and by_id["s-3"]["metrics"]
    assert found["meta"]["note"]["code"] == "partial_coverage"


async def test_runs_not_read_within_the_budget_come_back_as_partial_rows(api, monkeypatch):
    client, _ = api
    read = runs_api.read_trajectory

    async def slow(record, run_id, *args):
        if run_id == "s-3":
            await asyncio.sleep(5)
        return await read(record, run_id, *args)

    monkeypatch.setattr(runs_api, "read_trajectory", slow)
    monkeypatch.setattr(mcp_tools, "CALL_BUDGET_SECONDS", 0.5)
    stats = await call(client, "get_run_stats", agent="fake@canonical")
    assert stats["data"][0]["runs"] == 2
    assert stats["meta"]["unavailable"] == 1
    assert stats["meta"]["note"]["code"] == "partial_coverage"
    rows = (await call(client, "get_runs", agent="fake@canonical", run_ids=["s-3"]))["data"]
    assert rows[0]["error"] == "harness_timeout"
    assert "not read within this call's time budget" in rows[0]["detail"]


async def test_an_id_the_harness_rejects_as_malformed_is_not_found(api, monkeypatch):
    client, _ = api
    read = runs_api.read_trajectory

    async def strict(record, run_id, *args):
        if run_id == "not-a-uuid":
            raise HTTPException(status_code=422, detail="harness error")
        return await read(record, run_id, *args)

    monkeypatch.setattr(runs_api, "read_trajectory", strict)
    got = await call(client, "get_runs", agent="fake@canonical", run_ids=["not-a-uuid", "s-1"])
    assert got["data"][0] == {"id": "not-a-uuid", "note": {"code": "not_found"}}
    assert got["meta"] == {"count": 2, "matched": 1, "unmatched": 1}


async def test_the_instructions_name_only_registered_tools(api):
    client, _ = api
    async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
        registered = {t.name for t in await mcp.list_tools()}
    named = set(re.findall(r"\b(?:find|get|download)_[a-z_]+\b", mcp_descriptions.INSTRUCTIONS))
    assert named == registered


async def test_any_error_reading_one_run_fails_only_its_row(api, monkeypatch):
    client, _ = api
    read = runs_api.read_trajectory

    async def broken(record, run_id, *args):
        if run_id == "s-2":
            raise RuntimeError("blob store fell over")
        return await read(record, run_id, *args)

    monkeypatch.setattr(runs_api, "read_trajectory", broken)
    stats = await call(client, "get_run_stats", agent="fake@canonical")
    assert stats["data"][0]["runs"] == 2
    assert stats["meta"]["unavailable"] == 1


async def test_unread_runs_stay_in_a_failed_only_listing(api, monkeypatch):
    client, _ = api
    read = runs_api.read_trajectory

    async def slow(record, run_id, *args):
        if run_id == "s-3":
            await asyncio.sleep(5)
        return await read(record, run_id, *args)

    monkeypatch.setattr(runs_api, "read_trajectory", slow)
    monkeypatch.setattr(mcp_tools, "CALL_BUDGET_SECONDS", 0.5)
    found = await call(client, "find_runs", agent="fake@canonical", failed_only=True)
    by_id = {row["id"]: row for row in found["data"]}
    assert by_id["s-3"]["error"] == "harness_timeout"
    assert found["meta"]["total"] == len(found["data"])


async def test_listing_past_the_call_budget_is_a_coded_error(api, monkeypatch):
    client, _ = api
    resolve = runs_api.resolve_runs

    async def slow(*args, **kwargs):
        await asyncio.sleep(5)
        return await resolve(*args, **kwargs)

    monkeypatch.setattr(runs_api, "resolve_runs", slow)
    monkeypatch.setattr(mcp_tools, "CALL_BUDGET_SECONDS", 0.3)
    with pytest.raises(ToolError, match="^harness_timeout: "):
        async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
            await mcp.call_tool("get_run_stats", {"agent": "fake@canonical"})


async def test_an_unsafe_run_id_is_an_invalid_run_id(api):
    client, _ = api
    async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
        for tool, args in [
            ("get_trajectory", {"run_id": "../x"}),
            ("get_runs", {"run_ids": ["s-1", "../x"]}),
        ]:
            with pytest.raises(ToolError, match="^invalid_run_id: "):
                await mcp.call_tool(tool, {"agent": "fake@canonical", **args})


async def test_find_runs_reads_date_only_bounds_in_its_timezone(api):
    client, _ = api
    utc = await call(client, "find_runs", agent="fake@canonical", start_date="2026-10-21")
    berlin = await call(
        client,
        "find_runs",
        agent="fake@canonical",
        start_date="2026-10-21",
        timezone="Europe/Berlin",
    )
    # s-1 was created 2026-10-20 23:00 UTC, which is 2026-10-21 01:00 in Berlin.
    assert {r["id"] for r in utc["data"]} == {"s-2", "s-3"}
    assert {r["id"] for r in berlin["data"]} == {"s-1", "s-2", "s-3"}
    with pytest.raises(ToolError, match="^invalid_timezone: "):
        async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
            await mcp.call_tool("find_runs", {"agent": "fake@canonical", "timezone": "Mars/Base"})


async def test_get_runs_is_one_row_per_id_in_order(api):
    client, _ = api
    result = await call(
        client,
        "get_runs",
        agent="fake@canonical",
        run_ids=["s-3", "nope", "s-1"],
        include=["metrics", "turns"],
    )
    assert [r["id"] for r in result["data"]] == ["s-3", "nope", "s-1"]
    assert result["data"][1] == {"id": "nope", "note": {"code": "not_found"}}
    first = result["data"][0]
    assert first["metrics"]["opening_question"] == "Which documents mention the refund policy?"
    assert [t["status"] for t in first["turns"]] == ["ok", "failed"]
    assert set(first["turns"][0]["cost"]) == MONEY_KEYS
    assert result["meta"] == {
        "count": 3,
        "matched": 2,
        "unmatched": 1,
    }


async def test_run_stats_group_by(api):
    client, _ = api
    total = await call(client, "get_run_stats", agent="fake@canonical")
    (row,) = total["data"]
    assert row["group"] == {} and row["runs"] == 3 and row["failed_turns"] == 3
    assert set(row["cost"]) == MONEY_KEYS
    assert total["meta"]["group_by"] == ["none"] and total["meta"]["capped"] is False

    daily = await call(
        client,
        "get_run_stats",
        agent="fake@canonical",
        start_date="2026-10-21",
        group_by="day",
        timezone="Europe/Berlin",
    )
    assert [(r["group"], r["runs"]) for r in daily["data"]] == [({"day": "2026-10-21"}, 3)]

    tools = await call(client, "get_run_stats", agent="fake@canonical", group_by="tool")
    assert [(r["group"]["tool"], r["tool_errors"], r["runs"]) for r in tools["data"]] == [
        ("search_docs", 6, 3),
        ("Bash", 3, 3),
    ]

    every = await call(
        client,
        "get_run_stats",
        agent="fake@canonical",
        start_date="2026-10-21",
        group_by=["none", "day", "tool"],
        timezone="Europe/Berlin",
    )
    assert every["meta"]["group_by"] == ["none", "day", "tool"]
    assert [r["group"] for r in every["data"]] == [
        {},
        {"day": "2026-10-21"},
        {"tool": "search_docs"},
        {"tool": "Bash"},
    ]
    assert every["data"][0]["runs"] == row["runs"]
    assert tools["data"][0]["sample_error"] == "unknown_column: name"

    capped = await call(client, "get_run_stats", agent="fake@canonical", max_runs=2)
    assert capped["meta"]["runs_considered"] == 2 and capped["meta"]["capped"] is True


async def test_trajectory_and_files(api):
    client, _ = api
    page = await call(
        client,
        "get_trajectory",
        agent="fake@canonical",
        run_id="s-1",
        offset=1,
        limit=1,
        max_chars=100,
    )
    (step,) = page["data"]
    assert step["step_id"] == 2
    assert step["tool_calls"][0]["arguments"].endswith("chars]")
    assert page["meta"]["count"] == 1 and page["meta"]["total"] == 5
    assert page["meta"]["next_offset"] == 2 and page["meta"]["schema_version"] == "ATIF-v1.8"

    files = await call(client, "get_run_files", agent="fake@canonical", run_id="s-1")
    assert files["data"] == [{"path": "outputs/r.md", "size": 13}]
    text = await call(
        client, "download_run_file", agent="fake@canonical", run_id="s-1", path="outputs/r.md"
    )
    assert text["data"][0]["text"] == "# report\nbody"
    assert text["meta"]["has_more"] is False


@pytest.mark.parametrize(
    ("tool", "args", "code"),
    [
        ("find_runs", {"agent": "nope@canonical"}, "not_found"),
        (
            "find_runs",
            {"agent": "fake@canonical", "start_date": "21/10/2026"},
            "invalid_date_format",
        ),
        (
            "find_runs",
            {"agent": "fake@canonical", "start_date": "2026-10-22", "end_date": "2026-10-20"},
            "invalid_range",
        ),
        ("find_runs", {"agent": "fake@canonical", "limit": 0}, "invalid_limit"),
        ("find_runs", {"agent": "fake@canonical", "include": ["x"]}, "invalid_include"),
        ("get_runs", {"agent": "fake@canonical", "run_ids": []}, "no_ids_provided"),
        ("get_runs", {"agent": "fake@canonical", "run_ids": ["r"] * 201}, "too_many_ids"),
        ("find_runs", {"agent": "fake@canonical", "limit": 5001}, "invalid_limit"),
        (
            "find_runs",
            {"agent": "fake@canonical", "include": ["metrics"], "limit": 201},
            "invalid_limit",
        ),
        ("get_run_stats", {"agent": "fake@canonical", "timezone": "Mars"}, "invalid_timezone"),
        ("get_run_stats", {"agent": "fake@canonical", "group_by": "week"}, "invalid_group_by"),
        ("find_runs", {"agent": "fake"}, "invalid_agent_id"),
        ("get_run_files", {"agent": "fake", "run_id": "s-1"}, "invalid_agent_id"),
        ("find_runs", {"agent": "fake@canonical", "source": "prod"}, "invalid_source"),
        ("find_experiments", {"status": "done"}, "invalid_status"),
        (
            "download_run_file",
            {"agent": "fake@canonical", "run_id": "s-1", "path": "x", "max_chars": 0},
            "invalid_max_chars",
        ),
    ],
)
async def test_errors_are_coded(api, tool, args, code):
    client, _ = api
    async with mcp_client(client.app, bearer(client)) as mcp:  # type: ignore[attr-defined]
        with pytest.raises(ToolError, match=f"^{code}: "):
            await mcp.call_tool(tool, args)


async def test_experiment_tools(api):
    client, _ = api
    body = {
        "dataset": "finance@canonical",
        "agent": "fake@admin",
        "capabilities": FakeHarness().capabilities,
        "holder": HOLDER,
    }
    exp = (await client.post("/api/experiments", json=body)).json()
    run = {
        "harness_run_id": "sess-9",
        "status": "completed",
        "answer": "42",
        "started_at": "2026-10-05T00:00:00Z",
        "ended_at": "2026-10-05T00:00:01Z",
    }
    recorded = await client.post(
        f"/api/experiments/{exp['id']}/runs",
        json={"case_id": "case-2", "repetition": 1, "run": run, "holder": HOLDER},
    )
    assert recorded.status_code == 200, recorded.text

    found = await call(client, "find_experiments", limit=5)
    assert found["data"][0]["id"] == exp["id"] and "config" not in found["data"][0]
    full_last_page = await call(client, "find_experiments", limit=len(found["data"]))
    assert full_last_page["meta"]["has_more"] is False
    assert "next_offset" not in full_last_page["meta"]
    got = await call(
        client, "get_experiments", experiment_ids=[exp["id"], 999999], include=["scorecard"]
    )
    assert got["data"][0]["agent"] == "fake@admin" and "scorecard" in got["data"][0]
    assert got["data"][1]["note"]["code"] == "not_found"
    assert got["meta"] == {
        "count": 2,
        "matched": 1,
        "unmatched": 1,
    }
    runs = await call(client, "get_experiment_runs", experiment_id=exp["id"])
    assert runs["meta"]["agent"] == "fake@admin"
    assert [(r["case_id"], r["id"], r["answer"]) for r in runs["data"]] == [
        ("case-2", "sess-9", "42")
    ]


@asynccontextmanager
async def oauth_app(env: Env, tmp_path, key: RSAKeyPair, **oauth: str) -> AsyncIterator:
    cfg = env.deps.cfg.model_copy(  # type: ignore[union-attr]
        update={
            "mcp": McpConfig(
                oauth=McpOAuthConfig(
                    issuer=ISSUER, jwks_uri=f"{ISSUER}/.well-known/jwks.json", **oauth
                )
            )
        }
    )
    env.deps.cfg = cfg
    app = create_app(
        cfg,  # type: ignore[arg-type]
        blob_store=env.blob,
        runner_deps=env.deps,
        agents=env.deps.agents,  # type: ignore[arg-type]
        session_factory=env.session_factory,
        check_schema=False,
        holder=HOLDER,
        mcp_jwt_verifier=JWTVerifier(public_key=key.public_key, issuer=ISSUER, audience=RESOURCE),
    )
    async with app.router.lifespan_context(app):
        yield app


async def test_the_providers_own_scope_name_is_the_one_required(
    tmp_path, stub_judges, session_factory
):
    key = RSAKeyPair.generate()
    env = await seed_env(session_factory, tmp_path, fake_with_runs())
    async with oauth_app(env, tmp_path, key, scope="analytics:read") as app:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            meta = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
            assert meta["scopes_supported"] == ["analytics:read"]
            theirs = key.create_token(issuer=ISSUER, audience=RESOURCE, scopes=["analytics:read"])
            assert await mcp_status(http, theirs) == 200
            ours = key.create_token(issuer=ISSUER, audience=RESOURCE, scopes=["evalkit:read"])
            assert await mcp_status(http, ours) == 403


async def test_oauth_tokens_reach_mcp_and_evalkit_tokens_still_do(
    tmp_path, stub_judges, session_factory
):
    key = RSAKeyPair.generate()
    env = await seed_env(session_factory, tmp_path, fake_with_runs())
    async with oauth_app(env, tmp_path, key) as app:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            meta = (await http.get("/.well-known/oauth-protected-resource/mcp")).json()
            assert meta["resource"] == RESOURCE
            assert meta["authorization_servers"] == [f"{ISSUER}/"]
            assert meta["scopes_supported"] == ["evalkit:read"]

            unauthenticated = await http.post("/mcp", json={})
            challenge = unauthenticated.headers["www-authenticate"]
            assert f'resource_metadata="{METADATA}"' in challenge

            good = key.create_token(
                subject="u-1", issuer=ISSUER, audience=RESOURCE, scopes=["evalkit:read"]
            )
            async with mcp_client(app, good) as mcp:
                result = await mcp.call_tool("find_agents", {"workspace": "canonical"})
            assert result.structured_content["meta"] == {"count": 1}

            api_call = await http.get("/api/agents", headers={"Authorization": f"Bearer {good}"})
            assert api_call.status_code == 401

            no_scope = key.create_token(issuer=ISSUER, audience=RESOURCE, scopes=["other:read"])
            assert await mcp_status(http, no_scope) == 403
            wrong_audience = key.create_token(
                issuer=ISSUER, audience="https://gateway.example.test/mcp", scopes=["evalkit:read"]
            )
            assert await mcp_status(http, wrong_audience) == 401
            wrong_key = RSAKeyPair.generate().create_token(
                issuer=ISSUER, audience=RESOURCE, scopes=["evalkit:read"]
            )
            assert await mcp_status(http, wrong_key) == 401
            assert await mcp_status(http, good) == 200

            login = await http.post(
                "/api/auth/login", json={"username": "admin", "password": USERS["admin"]}
            )
            async with mcp_client(app, login.json()["token"]) as mcp:
                assert len(await mcp.list_tools()) == 10
