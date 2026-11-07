import json

import httpx2
from respx import MockResponse

from evalkit.contracts.custom_evaluator import EvalCase
from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.settings import RemoteEvaluatorSettings
from evalkit.core.custom_eval import EvalError, score_with_custom_evaluator
from evalkit.core.remote_evaluators import RemoteEvaluator

ENDPOINT = "https://client.test/evaluators/required-tool-calls"
CFG = RemoteEvaluator(
    name="required_tool_calls",
    workspace="dev",
    kind=EvaluatorKind.SCORE,
    settings=RemoteEvaluatorSettings(endpoint=ENDPOINT, timeout_seconds=1),
)

CASE = EvalCase(
    id="case-1",
    input={"question": "hi"},
    config={"tools": ["retrieve_passages"]},
)


async def test_happy_path_multi_score(httpx2_mock):
    route = httpx2_mock.post(ENDPOINT).mock(
        return_value=MockResponse(
            200,
            json={
                "scores": [
                    {"name": "required_tool_calls", "score": 0.5, "label": "partial"},
                    {"name": "tool_call_efficiency", "score": 1.0},
                ]
            },
        )
    )
    scores = await score_with_custom_evaluator(
        CFG, "client-key", case=CASE, answer="ans", trajectory={"schema_version": "ATIF-v1.8"}
    )
    assert isinstance(scores, list) and len(scores) == 2
    assert scores[0].score == 0.5
    body = route.calls[0].request
    assert body.headers["x-api-key"] == "client-key"


async def test_timeout_returns_error(httpx2_mock):
    httpx2_mock.post(ENDPOINT).mock(side_effect=httpx2.ConnectTimeout("timed out"))
    result = await score_with_custom_evaluator(CFG, "k", case=CASE, answer="a", trajectory={})
    assert isinstance(result, EvalError)
    assert result.evaluator == "required_tool_calls"


async def test_500_returns_error(httpx2_mock):
    httpx2_mock.post(ENDPOINT).mock(return_value=MockResponse(500, text="boom"))
    result = await score_with_custom_evaluator(CFG, "k", case=CASE, answer="a", trajectory={})
    assert isinstance(result, EvalError)


async def test_null_score_passes_through(httpx2_mock):
    httpx2_mock.post(ENDPOINT).mock(
        return_value=MockResponse(
            200, json={"scores": [{"name": "x", "score": None, "label": "skip"}]}
        )
    )
    scores = await score_with_custom_evaluator(CFG, "k", case=CASE, answer="a", trajectory={})
    assert isinstance(scores, list) and scores[0].score is None


async def test_invalid_response_shape_returns_error(httpx2_mock):
    httpx2_mock.post(ENDPOINT).mock(return_value=MockResponse(200, json={"nope": 1}))
    result = await score_with_custom_evaluator(CFG, "k", case=CASE, answer="a", trajectory={})
    assert isinstance(result, EvalError)


async def test_no_key_sends_no_header_and_gauge_wire_kind(httpx2_mock):
    route = httpx2_mock.post(ENDPOINT).mock(return_value=MockResponse(200, json={"scores": []}))
    gauge = CFG.model_copy(update={"kind": EvaluatorKind.GAUGE})
    await score_with_custom_evaluator(gauge, None, case=CASE, answer="a", trajectory={})
    request = route.calls[0].request
    assert "x-api-key" not in request.headers
    assert json.loads(request.content)["kind"] == "gauge"
