import json

import pytest
from respx import MockResponse

from evalkit.client.api import AsyncApi
from evalkit.client.oidc import static_auth
from evalkit.client.sessions import HttpSessionPuller, HttpSessionReader, sessions_for
from evalkit.contracts.settings import AgentSettings
from evalkit.core.agents import AgentDefinition
from evalkit.core.traces.session_import import SessionPullFailed
from evalkit.core.traces.sessions import SessionTooLarge


async def test_http_session_reader_round_trip(httpx2_mock):
    httpx2_mock.get("http://srv/api/agents/sdk%40me/sessions/s-1/trajectory").mock(
        return_value=MockResponse(
            200,
            json={
                "settled": True,
                "root_present": True,
                "span_count": 2,
                "trajectory": {"schema_version": "ATIF-v1.8", "steps": []},
            },
        )
    )
    api = AsyncApi("http://srv", static_auth("tok"))
    result = await HttpSessionReader(api).trajectory("sdk@me", "s-1")
    assert result.settled and result.span_count == 2 and result.trajectory["steps"] == []
    await api.aclose()


async def test_http_session_reader_raises_session_too_large_on_413(httpx2_mock):
    detail = "session s-1 of agent sdk@me holds 900.0 MiB of span attributes"
    httpx2_mock.get("http://srv/api/agents/sdk%40me/sessions/s-1/trajectory").mock(
        return_value=MockResponse(413, json={"detail": detail})
    )
    api = AsyncApi("http://srv", static_auth("tok"))
    with pytest.raises(SessionTooLarge, match="900.0 MiB"):
        await HttpSessionReader(api).trajectory("sdk@me", "s-1")
    await api.aclose()


SESSION = {"settled": True, "root_present": True, "span_count": 1}


async def test_a_trace_source_reader_pulls_then_reads(httpx2_mock):
    pull = httpx2_mock.post("http://srv/api/agents/sdk%40me/sessions/import").mock(
        return_value=MockResponse(200, json={"imported": 1})
    )
    httpx2_mock.get("http://srv/api/agents/sdk%40me/sessions/s-1/trajectory").mock(
        return_value=MockResponse(200, json=SESSION)
    )
    api = AsyncApi("http://srv", static_auth("tok"))
    definition = AgentDefinition(
        id=1,
        name="sdk",
        workspace="me",
        workspace_is_shared=False,
        harness_url=None,
        trajectory_source="otel",
        repo_url=None,
        settings=AgentSettings(trace_source="phx", project="ra"),
    )
    result = await sessions_for(api, definition).trajectory("sdk@me", "s-1")
    assert result.settled and json.loads(pull.calls[0].request.content) == {"session_id": "s-1"}
    await api.aclose()


async def test_a_failed_pull_raises_session_pull_failed(httpx2_mock):
    httpx2_mock.post("http://srv/api/agents/sdk%40me/sessions/import").mock(
        return_value=MockResponse(400, json={"detail": "phx: refused"})
    )
    api = AsyncApi("http://srv", static_auth("tok"))
    with pytest.raises(SessionPullFailed, match="phx: refused"):
        await HttpSessionPuller(api)("sdk@me", "s-1")
    await api.aclose()
