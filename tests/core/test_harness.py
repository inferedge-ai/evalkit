from datetime import UTC, datetime

import httpx
import pytest

from evalkit.contracts.atif import TrajectoryValidationError
from evalkit.contracts.harness import FileRef, RunListParams, RunMetadata
from evalkit.contracts.settings import AgentSettings
from evalkit.core.harness import KEEPALIVE_EXPIRY_SECONDS, HarnessClient, RunTimeout
from evalkit.core.paths import UnsafePathError
from tests.fake_agents import fake_agent
from tests.fake_harness import FakeHarness


def make_client(fake: FakeHarness, **cfg_overrides) -> HarnessClient:
    settings = AgentSettings(
        **{"poll_interval_seconds": 0, "run_timeout_seconds": 1, **cfg_overrides}
    )
    agent = fake_agent(settings=settings)
    return HarnessClient(agent, "client-key", transport=httpx.ASGITransport(app=fake.app))


async def test_start_poll_completed():
    fake = FakeHarness(answer="hello", complete_after_polls=2)
    client = make_client(fake)
    run_id = await client.start_run(
        {"question": "hi"}, metadata=RunMetadata(experiment_id="e1", case_id="c1", repetition=1)
    )
    status = await client.poll_until_terminal(run_id)
    assert status.status == "completed"
    assert status.answer == "hello"
    assert fake.received_payloads[run_id]["metadata"]["case_id"] == "c1"
    assert fake.stop_calls == []


async def test_multipart_run_with_file():
    fake = FakeHarness()
    client = make_client(fake)
    run_id = await client.start_run({"question": "hi"}, files=[("doc.pdf", b"%PDF fake")])
    assert fake.received_files[run_id] == [("doc.pdf", b"%PDF fake")]
    assert fake.received_payloads[run_id]["input"] == {"question": "hi"}


async def test_json_run_with_file_urls():
    fake = FakeHarness(capabilities_overrides={"accepts": {"file_urls": True}})
    client = make_client(fake)
    run_id = await client.start_run(
        {"question": "hi"},
        file_refs=[FileRef(name="a.csv", url="s3://b/fixtures/a.csv", context="Prices.")],
    )
    assert fake.received_file_urls[run_id] == [
        {"name": "a.csv", "url": "s3://b/fixtures/a.csv", "context": "Prices."}
    ]
    assert run_id not in fake.received_files
    assert fake.received_payloads[run_id]["input"] == {"question": "hi"}


async def test_start_run_error_includes_response_body():
    fake = FakeHarness(reject_multipart="Invalid input: expected object, received undefined")
    client = make_client(fake)
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.start_run({"question": "hi"}, files=[("doc.csv", b"a,b")])
    message = str(exc_info.value)
    assert "400" in message
    assert "POST http://fake/evalkit/v1/runs" in message
    assert "expected object, received undefined" in message


async def test_timeout_calls_stop_when_accepted():
    fake = FakeHarness(complete_after_polls=10_000)
    client = make_client(fake, run_timeout_seconds=0)
    run_id = await client.start_run({"question": "hi"})
    with pytest.raises(RunTimeout):
        await client.poll_until_terminal(run_id)
    assert fake.stop_calls == [run_id]


async def test_timeout_waits_for_terminal_after_stop():
    fake = FakeHarness(complete_after_polls=10_000, stop_settles_after_polls=3)
    client = make_client(fake, run_timeout_seconds=0, stop_grace_seconds=5)
    run_id = await client.start_run({"question": "hi"})
    with pytest.raises(RunTimeout) as exc_info:
        await client.poll_until_terminal(run_id)
    assert exc_info.value.last_status.status == "cancelled"
    assert fake.runs[run_id]["polls_after_stop"] == 4


async def test_post_stop_wait_is_capped():
    fake = FakeHarness(complete_after_polls=10_000, stop_settles_after_polls=10_000)
    client = make_client(fake, run_timeout_seconds=0, stop_grace_seconds=0)
    run_id = await client.start_run({"question": "hi"})
    with pytest.raises(RunTimeout):
        await client.poll_until_terminal(run_id)
    assert fake.runs[run_id]["polls_after_stop"] == 0


async def test_timeout_never_stops_when_not_accepted():
    fake = FakeHarness(
        complete_after_polls=10_000, capabilities_overrides={"accepts": {"stop": False}}
    )
    client = make_client(fake, run_timeout_seconds=0)
    run_id = await client.start_run({"question": "hi"})
    with pytest.raises(RunTimeout):
        await client.poll_until_terminal(run_id)
    assert fake.stop_calls == []


async def test_trajectory_validation_error_surfaces():
    fake = FakeHarness(trajectory_doc={"schema_version": "bogus", "steps": []})
    client = make_client(fake)
    run_id = await client.start_run({"question": "hi"})
    with pytest.raises(TrajectoryValidationError):
        await client.trajectory(run_id)


async def test_trajectory_valid():
    fake = FakeHarness()
    client = make_client(fake)
    run_id = await client.start_run({"question": "hi"})
    doc = await client.trajectory(run_id)
    assert doc.agent.name == "myagent"


async def test_bearer_enforced():
    fake = FakeHarness(api_key="other-key")
    client = make_client(fake)
    with pytest.raises(httpx.HTTPStatusError) as err:
        await client.capabilities()
    assert err.value.response.status_code == 401


async def test_list_runs_and_files():
    fake = FakeHarness(workspace_files={"outputs/report.md": b"# report"})
    client = make_client(fake)
    run_id = await client.start_run({"question": "hi"})
    runs = await client.list_runs()
    assert runs.total == 1 and runs.runs[0].run_id == run_id
    files = await client.list_files(run_id)
    assert files.files[0].path == "outputs/report.md"
    assert await client.get_file(run_id, "outputs/report.md") == b"# report"


async def test_stream_file_yields_the_body_and_raises_before_yielding():
    big = bytes(range(256)) * 8192
    fake = FakeHarness(workspace_files={"out/big.bin": big})
    client = make_client(fake)
    run_id = await client.start_run({"question": "hi"})
    file = await client.stream_file(run_id, "out/big.bin")
    assert file.size == len(big)
    assert b"".join([chunk async for chunk in file.chunks]) == big
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.stream_file(run_id, "missing.bin")
    assert exc.value.response.status_code == 404
    with pytest.raises(UnsafePathError):
        await client.stream_file(run_id, "../etc/passwd")
    await client.aclose()


async def test_list_runs_passes_time_window():
    fake = FakeHarness()
    client = make_client(fake)
    await client.start_run({"question": "hi"})
    after = await client.list_runs(RunListParams(since=datetime(2026, 9, 2, tzinfo=UTC)))
    before = await client.list_runs(RunListParams(until=datetime(2026, 9, 2, tzinfo=UTC)))
    assert after.runs == [] and len(before.runs) == 1


def test_run_list_params_serialises_times_as_utc_iso():
    query = RunListParams(since=datetime(2026, 10, 1, tzinfo=UTC)).as_query()
    assert query["since"] == "2026-10-01T00:00:00Z" and "until" not in query


async def test_multipart_payload_carries_file_context():
    fake = FakeHarness()
    client = make_client(fake)
    run_id = await client.start_run(
        {"question": "hi"},
        files=[("a.csv", b"x,y")],
        file_refs=[FileRef(name="a.csv", context="Prices.")],
    )
    assert fake.received_files[run_id] == [("a.csv", b"x,y")]
    assert fake.received_payloads[run_id]["files"] == [{"name": "a.csv", "context": "Prices."}]


STALE_CONNECTION_ERRORS = [
    httpx.RemoteProtocolError("Server disconnected without sending a response."),
    httpx.ReadError(""),
]


class DisconnectingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        drops: int,
        error: httpx.TransportError,
        method: str = "GET",
    ):
        self.inner = inner
        self.drops = drops
        self.error = error
        self.method = method
        self.dropped = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == self.method and self.dropped < self.drops:
            self.dropped += 1
            raise type(self.error)(str(self.error), request=request)
        return await self.inner.handle_async_request(request)


def make_disconnecting_client(
    fake: FakeHarness, drops: int, error: httpx.TransportError, method: str = "GET"
) -> tuple[HarnessClient, DisconnectingTransport]:
    transport = DisconnectingTransport(httpx.ASGITransport(app=fake.app), drops, error, method)
    agent = fake_agent(settings=AgentSettings(poll_interval_seconds=0, run_timeout_seconds=1))
    return HarnessClient(agent, "client-key", transport=transport), transport


@pytest.mark.parametrize("error", STALE_CONNECTION_ERRORS, ids=type)
async def test_get_retries_after_server_disconnect(error):
    fake = FakeHarness(answer="hello")
    client, transport = make_disconnecting_client(fake, drops=2, error=error)
    run_id = await client.start_run({"question": "hi"})
    status = await client.poll_until_terminal(run_id)
    assert status.answer == "hello"
    assert transport.dropped == 2


@pytest.mark.parametrize("error", STALE_CONNECTION_ERRORS, ids=type)
async def test_stream_file_retries_after_server_disconnect(error):
    fake = FakeHarness(workspace_files={"out/a.txt": b"hello"})
    client, transport = make_disconnecting_client(fake, drops=0, error=error)
    run_id = await client.start_run({"question": "hi"})
    transport.drops = 2
    file = await client.stream_file(run_id, "out/a.txt")
    assert b"".join([chunk async for chunk in file.chunks]) == b"hello"
    assert transport.dropped == 2
    await client.aclose()


@pytest.mark.parametrize("error", STALE_CONNECTION_ERRORS, ids=type)
async def test_get_gives_up_after_repeated_disconnects(error):
    fake = FakeHarness()
    client, _ = make_disconnecting_client(fake, drops=10, error=error)
    run_id = await client.start_run({"question": "hi"})
    with pytest.raises(type(error)):
        await client.status(run_id)


@pytest.mark.parametrize("error", STALE_CONNECTION_ERRORS, ids=type)
async def test_post_is_not_retried_after_server_disconnect(error):
    fake = FakeHarness()
    client, transport = make_disconnecting_client(fake, drops=1, error=error, method="POST")
    with pytest.raises(type(error)):
        await client.start_run({"question": "hi"})
    assert transport.dropped == 1
    assert fake.runs == {}


async def test_idle_connections_expire_before_common_server_keepalives():
    client = HarnessClient(fake_agent(), "client-key")
    pool = client._client._transport._pool
    assert pool._keepalive_expiry == KEEPALIVE_EXPIRY_SECONDS
    await client.aclose()


async def test_file_urls_percent_encode_the_run_id_and_each_segment():
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path)
        return httpx.Response(200, content=b"ok")

    client = HarnessClient(fake_agent(), "client-key", transport=httpx.MockTransport(handler))
    assert await client.get_file("r 1?x", "out dir/a#b.md") == b"ok"
    assert seen == [b"/evalkit/v1/runs/r%201%3Fx/files/out%20dir/a%23b.md"]


@pytest.mark.parametrize(
    ("run_id", "path"),
    [
        ("r", "../internal"),
        ("r", "a/../../x"),
        ("r", "/../etc/passwd"),
        ("r", "a//b"),
        ("..", "a"),
        ("a/b", "c"),
    ],
)
async def test_file_requests_refuse_unsafe_paths(run_id, path):
    client = HarnessClient(
        fake_agent(), "client-key", transport=httpx.MockTransport(lambda r: httpx.Response(200))
    )
    with pytest.raises(UnsafePathError):
        await client.get_file(run_id, path)
