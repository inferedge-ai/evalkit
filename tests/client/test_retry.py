import httpx2
import pytest

from evalkit.client import retry as retry_module
from evalkit.client.retry import Retry, RetryTransport


def top(low: float, high: float) -> float:
    return high


def bottom(low: float, high: float) -> float:
    return low


READ = Retry(
    total=3,
    allowed_methods=("GET", "HEAD"),
    status_forcelist=(502, 503, 504),
    retry_on_exceptions=(httpx2.NetworkError, httpx2.ConnectTimeout),
    backoff_factor=0.5,
    max_backoff_wait=2.0,
    jitter=top,
)
WRITE = Retry(
    total=3,
    allowed_methods=("GET", "HEAD", "POST"),
    status_forcelist=(502, 503, 504),
    retry_on_exceptions=(httpx2.NetworkError,),
    backoff_factor=0.5,
    max_backoff_wait=2.0,
    jitter=top,
)


@pytest.fixture
def waits(monkeypatch) -> list[float]:
    seen: list[float] = []
    monkeypatch.setattr(retry_module.time, "sleep", seen.append)

    async def record(seconds: float) -> None:
        seen.append(seconds)

    monkeypatch.setattr(retry_module.anyio, "sleep", record)
    return seen


def scripted(outcomes: list, seen: list[str]):
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request.method)
        outcome = outcomes[len(seen) - 1] if len(seen) <= len(outcomes) else httpx2.Response(200)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    return httpx2.MockTransport(handler)


def sync_client(outcomes: list, seen: list[str], retry: Retry = READ) -> httpx2.Client:
    return httpx2.Client(
        base_url="http://t", transport=RetryTransport(scripted(outcomes, seen), retry)
    )


def async_client(outcomes: list, seen: list[str], retry: Retry = READ) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url="http://t", transport=RetryTransport(scripted(outcomes, seen), retry)
    )


def test_sync_retries_a_503_then_returns_200(waits):
    seen: list[str] = []
    with sync_client([httpx2.Response(503)], seen) as client:
        assert client.get("/x").status_code == 200
    assert seen == ["GET", "GET"]
    assert waits == [1.0]


async def test_async_retries_a_503_then_returns_200(waits):
    seen: list[str] = []
    async with async_client([httpx2.Response(503)], seen) as client:
        assert (await client.get("/x")).status_code == 200
    assert seen == ["GET", "GET"]
    assert waits == [1.0]


def test_sync_gives_up_after_total_and_returns_last_503(waits):
    seen: list[str] = []
    with sync_client([httpx2.Response(503)] * 10, seen) as client:
        assert client.get("/x").status_code == 503
    assert len(seen) == 4
    assert waits == [1.0, 2.0, 2.0]


async def test_async_gives_up_after_total_and_returns_last_503(waits):
    seen: list[str] = []
    async with async_client([httpx2.Response(503)] * 10, seen) as client:
        assert (await client.get("/x")).status_code == 503
    assert len(seen) == 4


def test_status_outside_forcelist_is_returned_at_once(waits):
    seen: list[str] = []
    with sync_client([httpx2.Response(500)], seen) as client:
        assert client.get("/x").status_code == 500
    assert seen == ["GET"]
    assert waits == []


def test_post_is_not_retried_under_the_read_policy(waits):
    seen: list[str] = []
    with sync_client([httpx2.Response(503)], seen) as client:
        assert client.post("/x").status_code == 503
    assert seen == ["POST"]


def test_post_is_retried_when_the_request_carries_the_write_policy(waits):
    seen: list[str] = []
    with sync_client([httpx2.Response(503)], seen) as client:
        assert client.post("/x", extensions={"retry": WRITE}).status_code == 200
    assert seen == ["POST", "POST"]


async def test_async_post_is_retried_under_the_write_policy(waits):
    seen: list[str] = []
    async with async_client([httpx2.Response(503)], seen) as client:
        assert (await client.post("/x", extensions={"retry": WRITE})).status_code == 200
    assert seen == ["POST", "POST"]


def test_total_zero_never_retries(waits):
    seen: list[str] = []
    with sync_client([httpx2.Response(503)], seen) as client:
        assert client.get("/x", extensions={"retry": Retry(total=0)}).status_code == 503
    assert seen == ["GET"]


def test_retries_connect_error_and_reraises_after_total(waits):
    seen: list[str] = []
    errors = [httpx2.ConnectError("refused")] * 10
    with sync_client(errors, seen) as client:
        with pytest.raises(httpx2.ConnectError):
            client.get("/x")
    assert len(seen) == 4
    assert waits == [1.0, 2.0, 2.0]


async def test_async_retries_connect_error_and_reraises_after_total(waits):
    seen: list[str] = []
    errors = [httpx2.ConnectError("refused")] * 10
    async with async_client(errors, seen) as client:
        with pytest.raises(httpx2.ConnectError):
            await client.get("/x")
    assert len(seen) == 4


def test_connect_error_then_success(waits):
    seen: list[str] = []
    with sync_client([httpx2.ConnectError("refused")], seen) as client:
        assert client.get("/x").status_code == 200
    assert seen == ["GET", "GET"]


def test_exception_outside_retry_on_exceptions_propagates_at_once(waits):
    seen: list[str] = []
    with sync_client([httpx2.ReadTimeout("slow")], seen) as client:
        with pytest.raises(httpx2.ReadTimeout):
            client.get("/x")
    assert seen == ["GET"]
    assert waits == []


def test_non_http_exception_propagates_at_once(waits):
    seen: list[str] = []
    with sync_client([ValueError("bug")], seen) as client:
        with pytest.raises(ValueError):
            client.get("/x")
    assert seen == ["GET"]


def test_retry_after_seconds_sets_the_wait(waits):
    seen: list[str] = []
    busy = httpx2.Response(503, headers={"Retry-After": "2"})
    with sync_client([busy], seen) as client:
        assert client.get("/x").status_code == 200
    assert waits == [2.0]


def test_retry_after_is_capped_at_max_backoff_wait(waits):
    seen: list[str] = []
    busy = httpx2.Response(503, headers={"Retry-After": "120"})
    with sync_client([busy], seen) as client:
        client.get("/x")
    assert waits == [2.0]


def test_retry_after_http_date_sets_the_wait(waits):
    seen: list[str] = []
    busy = httpx2.Response(503, headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})
    with sync_client([busy], seen) as client:
        client.get("/x")
    assert waits == [1.0]


def test_invalid_retry_after_falls_back_to_backoff(waits):
    seen: list[str] = []
    busy = httpx2.Response(503, headers={"Retry-After": "soon"})
    with sync_client([busy], seen) as client:
        client.get("/x")
    assert waits == [1.0]


async def test_async_retry_after_sets_the_wait(waits):
    seen: list[str] = []
    busy = httpx2.Response(503, headers={"Retry-After": "2"})
    async with async_client([busy], seen) as client:
        await client.get("/x")
    assert waits == [2.0]


def test_backoff_grows_and_caps_at_max_backoff_wait():
    retry = Retry(total=9, backoff_factor=0.5, max_backoff_wait=3.0, jitter=top)
    assert [retry.backoff(attempt) for attempt in range(5)] == [1.0, 2.0, 3.0, 3.0, 3.0]


def test_backoff_is_a_uniform_draw_up_to_the_exponential_bound():
    draws: list[tuple[float, float]] = []

    def record(low: float, high: float) -> float:
        draws.append((low, high))
        return 0.25

    retry = Retry(total=9, backoff_factor=0.5, max_backoff_wait=100.0, jitter=record)
    assert retry.backoff(2) == 0.5 * 2**3 * 0.25
    assert draws == [(0, 1)]


def test_backoff_can_be_zero_with_the_lowest_draw():
    assert Retry(backoff_factor=0.5, jitter=bottom).backoff(3) == 0.0


def test_default_jitter_stays_within_bounds():
    retry = Retry(backoff_factor=0.5, max_backoff_wait=3.0)
    assert all(0.0 <= retry.backoff(1) <= 2.0 for _ in range(50))


def test_non_replayable_stream_body_is_sent_once(waits):
    seen: list[str] = []

    def chunks():
        yield b"abc"

    with sync_client([httpx2.Response(503)], seen) as client:
        response = client.post("/x", content=chunks(), extensions={"retry": WRITE})
    assert response.status_code == 503
    assert seen == ["POST"]
