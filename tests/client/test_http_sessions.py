import httpx
import pytest
import respx

from evalkit.client.api import AsyncApi
from evalkit.client.sessions import HttpSessionReader
from evalkit.core.traces.sessions import SessionTooLarge


@respx.mock
async def test_http_session_reader_round_trip():
    respx.get("http://srv/api/agents/sdk%40me/sessions/s-1/trajectory").mock(
        return_value=httpx.Response(
            200,
            json={
                "settled": True,
                "root_present": True,
                "span_count": 2,
                "trajectory": {"schema_version": "ATIF-v1.8", "steps": []},
            },
        )
    )
    api = AsyncApi("http://srv", "tok")
    result = await HttpSessionReader(api).trajectory("sdk@me", "s-1")
    assert result.settled and result.span_count == 2 and result.trajectory["steps"] == []
    await api.aclose()


@respx.mock
async def test_http_session_reader_raises_session_too_large_on_413():
    detail = "session s-1 of agent sdk@me holds 900.0 MiB of span attributes"
    respx.get("http://srv/api/agents/sdk%40me/sessions/s-1/trajectory").mock(
        return_value=httpx.Response(413, json={"detail": detail})
    )
    api = AsyncApi("http://srv", "tok")
    with pytest.raises(SessionTooLarge, match="900.0 MiB"):
        await HttpSessionReader(api).trajectory("sdk@me", "s-1")
    await api.aclose()
