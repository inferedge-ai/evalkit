import httpx2
import pytest

from evalkit.client.api import ApiRequestError, AsyncApi
from evalkit.client.server_evaluators import HttpServerEvaluators


async def test_server_evaluator_calls_are_never_retried_and_get_a_long_timeout(monkeypatch):
    monkeypatch.setenv("EVALKIT_SERVER_EVALUATOR_TIMEOUT", "1234")
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(503, json={"detail": "busy"})

    api = AsyncApi("http://test", transport=httpx2.MockTransport(handler))
    with pytest.raises(ApiRequestError, match="503"):
        await HttpServerEvaluators(api).score(7, "answer_rubrics", {"rubrics": []})
    await api.aclose()
    assert len(seen) == 1
    assert seen[0].url.path == "/api/experiment-runs/7/server-evaluations/answer_rubrics"
    assert seen[0].extensions["timeout"]["read"] == 1234
