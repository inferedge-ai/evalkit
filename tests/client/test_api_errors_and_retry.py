import httpx
import pytest

from evalkit.client.api import ApiRequestError, AsyncApi, SyncApi
from evalkit.client.blob import HttpBlobStore
from evalkit.client.store import HttpStore
from evalkit.contracts.enums import EvaluationSource, EvaluatorKind
from evalkit.store.base import EvaluationRow

PROXY_502 = "<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


def flaky(failures: list[httpx.Response | Exception], seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if failures:
            failure = failures.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        return httpx.Response(200, json={"ok": True})

    return handler


def proxy_502() -> httpx.Response:
    return httpx.Response(502, text=PROXY_502, headers={"content-type": "text/html"})


async def test_get_retries_gateway_errors_and_connection_errors():
    seen: list[str] = []
    failures = [
        proxy_502(),
        httpx.Response(503),
        httpx.ConnectError("refused"),
        httpx.Response(504),
    ]
    api = AsyncApi("http://t", None, transport=httpx.MockTransport(flaky(failures, seen)))
    resp = await api.get("/api/health")
    assert resp.json() == {"ok": True}
    assert len(seen) == 5


async def test_get_gives_up_after_the_last_attempt_with_a_short_message():
    seen: list[str] = []
    failures = [proxy_502() for _ in range(6)]
    api = AsyncApi("http://t", None, transport=httpx.MockTransport(flaky(failures, seen)))
    with pytest.raises(ApiRequestError) as raised:
        await api.get("/api/health")
    assert len(seen) == 5
    assert raised.value.status == 502
    assert str(raised.value) == "GET /api/health → 502: Bad Gateway"


async def test_connection_error_reraised_after_the_last_attempt():
    seen: list[str] = []
    failures: list[httpx.Response | Exception] = [httpx.ConnectError("refused") for _ in range(6)]
    api = AsyncApi("http://t", None, transport=httpx.MockTransport(flaky(failures, seen)))
    with pytest.raises(httpx.ConnectError):
        await api.get("/api/health")
    assert len(seen) == 5


async def test_creating_posts_and_server_errors_are_not_retried():
    seen: list[str] = []
    api = AsyncApi("http://t", None, transport=httpx.MockTransport(flaky([proxy_502()], seen)))
    with pytest.raises(ApiRequestError):
        await api.post("/api/experiments/e/runs", json={})
    seen.clear()
    api = AsyncApi(
        "http://t", None, transport=httpx.MockTransport(flaky([httpx.Response(500)], seen))
    )
    with pytest.raises(ApiRequestError):
        await api.get("/api/health")
    assert seen == ["GET /api/health"]


async def test_idempotent_writes_retry():
    seen: list[str] = []
    failures: list[httpx.Response | Exception] = [
        proxy_502(),
        httpx.Response(503),
        httpx.ReadError("reset"),
        httpx.Response(504),
    ]
    api = AsyncApi("http://t", None, transport=httpx.MockTransport(flaky(failures, seen)))
    store = HttpStore(api)
    row = EvaluationRow(
        producer="s", name="s", kind=EvaluatorKind.SCORE, source=EvaluationSource.BUILTIN
    )
    await store.publish_evaluations(7, [row], force=False)
    await store.set_model(1, "m")
    await HttpBlobStore(api, 1).put_object("trajectories/3/x.json", b"{}")
    assert seen == [
        *["POST /api/experiment-runs/7/evaluations"] * 5,
        "PATCH /api/experiments/1",
        "PUT /api/experiments/1/blob/trajectories/3/x.json",
    ]


async def test_json_detail_is_kept():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "unknown run"})

    api = AsyncApi("http://t", None, transport=httpx.MockTransport(handler))
    with pytest.raises(ApiRequestError) as raised:
        await api.get("/api/experiments/e")
    assert str(raised.value) == "GET /api/experiments/e → 404: unknown run"


async def test_empty_json_error_body_gets_the_short_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=b"", headers={"content-type": "application/json"})

    api = AsyncApi("http://t", None, transport=httpx.MockTransport(handler))
    with pytest.raises(ApiRequestError) as raised:
        await api.get("/api/x")
    assert str(raised.value) == "GET /api/x → 500: Internal Server Error"


def test_sync_api_retries_get_and_head_but_not_delete():
    seen: list[str] = []
    failures = [proxy_502(), httpx.Response(200), httpx.Response(503)]
    api = SyncApi("http://t", transport=httpx.MockTransport(flaky(failures, seen)))
    assert api.get("/api/datasets").status_code == 200
    assert api.head("/api/fixtures/a").status_code == 200
    assert seen == [
        "GET /api/datasets",
        "GET /api/datasets",
        "HEAD /api/fixtures/a",
        "HEAD /api/fixtures/a",
    ]
    seen.clear()
    api = SyncApi("http://t", transport=httpx.MockTransport(flaky([proxy_502()], seen)))
    with pytest.raises(ApiRequestError) as raised:
        api.delete("/api/users/a")
    assert seen == ["DELETE /api/users/a"]
    assert "<html" not in str(raised.value)
