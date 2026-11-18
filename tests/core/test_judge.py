import json

import httpx2
import pytest
from pydantic import BaseModel, ValidationError
from respx import MockResponse

from evalkit.contracts.settings import JudgeSettings
from evalkit.core import judge
from evalkit.core.judge import call_judge

CFG = JudgeSettings(
    base_url="https://judge.test/v1",
    model="vertex_ai/gemini-3.6-flash",
)


@pytest.fixture(autouse=True)
def fresh_adaptations():
    judge._ADAPTATIONS.clear()
    yield
    judge._ADAPTATIONS.clear()


MAX_TOKENS_REJECTED = MockResponse(
    400,
    json={
        "error": {
            "message": "Unsupported parameter: 'max_tokens' is not supported with this "
            "model. Use 'max_completion_tokens' instead.",
            "type": "invalid_request_error",
            "param": "max_tokens",
            "code": "unsupported_parameter",
        }
    },
)


def too_large(key: str, limit: int) -> MockResponse:
    return MockResponse(
        400,
        json={
            "error": {
                "message": f"{key} is too large: 32768. This model supports at most "
                f"{limit} completion tokens, whereas you provided 32768.",
                "type": "invalid_request_error",
                "param": key,
                "code": "invalid_value",
            }
        },
    )


async def judge_once(cfg: JudgeSettings = CFG, model: str | None = None) -> "Verdict":
    verdict, _ = await call_judge(
        cfg,
        api_key="k",
        system_prompt="sys",
        user_prompt="user",
        output_type=Verdict,
        evaluator_name="answer_rubrics",
        model=model,
    )
    return verdict


class Verdict(BaseModel):
    score: float
    reason: str


def completion(content: str) -> dict:
    return {
        "id": "c1",
        "object": "chat.completion",
        "created": 0,
        "model": CFG.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


async def test_request_shape_and_parse(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions").mock(
        return_value=MockResponse(200, json=completion('{"score": 0.8, "reason": "ok"}'))
    )
    verdict, usage = await call_judge(
        CFG,
        api_key="k",
        system_prompt="sys",
        user_prompt="user",
        output_type=Verdict,
        session_id="sess-1",
        evaluator_name="answer_rubrics",
    )
    assert verdict.score == 0.8
    assert usage.input_tokens == 100 and usage.output_tokens == 20
    request = route.calls[0].request
    body = json.loads(request.content)
    assert body["max_tokens"] == 32768
    assert body["reasoning_effort"] == "high"
    assert body["response_format"] == {
        "type": "json_schema",
        "json_schema": {"name": "Verdict", "schema": Verdict.model_json_schema()},
    }
    assert request.headers["x-litellm-session-id"] == "sess-1"
    assert request.headers["x-litellm-tags"] == "eval-judge,evaluator:answer_rubrics"


async def test_one_repair_retry(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        MockResponse(200, json=completion("not json at all")),
        MockResponse(200, json=completion('{"score": 1.0, "reason": "fixed"}')),
    ]
    verdict, usage = await call_judge(
        CFG,
        api_key="k",
        system_prompt="sys",
        user_prompt="user",
        output_type=Verdict,
        session_id=None,
        evaluator_name="x",
    )
    assert verdict.score == 1.0
    assert route.call_count == 2
    assert usage.input_tokens == 200
    first = json.loads(route.calls[0].request.content)
    second = json.loads(route.calls[1].request.content)
    assert second["messages"][:2] == first["messages"]
    assert second["messages"][2] == {"role": "assistant", "content": "not json at all"}
    assert second["messages"][3]["role"] == "user"
    assert "corrected JSON object" in second["messages"][3]["content"]


async def test_max_tokens_falls_back_to_max_completion_tokens(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        MockResponse(
            400,
            json={
                "error": {
                    "message": "Unsupported parameter: 'max_tokens' is not supported with this "
                    "model. Use 'max_completion_tokens' instead.",
                    "type": "invalid_request_error",
                    "param": "max_tokens",
                    "code": "unsupported_parameter",
                }
            },
        ),
        MockResponse(200, json=completion('{"score": 0.9, "reason": "ok"}')),
    ]
    verdict, _ = await call_judge(
        CFG,
        api_key="k",
        system_prompt="sys",
        user_prompt="user",
        output_type=Verdict,
        evaluator_name="answer_rubrics",
    )
    assert verdict.score == 0.9
    assert route.call_count == 2
    first = json.loads(route.calls[0].request.content)
    second = json.loads(route.calls[1].request.content)
    assert first["max_tokens"] == 32768
    assert "max_completion_tokens" not in first
    assert second["max_completion_tokens"] == 32768
    assert "max_tokens" not in second
    assert second["reasoning_effort"] == "high"


@pytest.mark.parametrize(
    "message",
    [
        "Unrecognized request argument supplied: reasoning_effort",
        "Unsupported parameter: 'reasoning_effort' is not supported with this model.",
    ],
)
async def test_reasoning_effort_is_dropped_when_unsupported(httpx2_mock, message):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        MockResponse(
            400,
            json={
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "param": None,
                    "code": None,
                }
            },
        ),
        MockResponse(200, json=completion('{"score": 0.7, "reason": "ok"}')),
    ]
    verdict, _ = await call_judge(
        CFG,
        api_key="k",
        system_prompt="sys",
        user_prompt="user",
        output_type=Verdict,
        evaluator_name="answer_rubrics",
    )
    assert verdict.score == 0.7
    assert route.call_count == 2
    second = json.loads(route.calls[1].request.content)
    assert second["max_tokens"] == 32768
    assert "reasoning_effort" not in second


async def test_both_unsupported_parameters_are_adapted(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        MockResponse(
            400,
            json={
                "error": {
                    "message": "Unsupported parameter: 'max_tokens' is not supported with this "
                    "model. Use 'max_completion_tokens' instead.",
                }
            },
        ),
        MockResponse(
            400,
            json={"error": {"message": "Unrecognized request argument supplied: reasoning_effort"}},
        ),
        MockResponse(200, json=completion('{"score": 0.6, "reason": "ok"}')),
    ]
    verdict, _ = await call_judge(
        CFG,
        api_key="k",
        system_prompt="sys",
        user_prompt="user",
        output_type=Verdict,
        evaluator_name="answer_rubrics",
    )
    assert verdict.score == 0.6
    assert route.call_count == 3
    third = json.loads(route.calls[2].request.content)
    assert third["max_completion_tokens"] == 32768
    assert "max_tokens" not in third
    assert "reasoning_effort" not in third


async def test_an_unrelated_400_is_not_retried(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions").mock(
        return_value=MockResponse(
            400,
            json={"error": {"message": "reasoning_effort must be one of low, medium or high"}},
        )
    )
    with pytest.raises(httpx2.HTTPStatusError):
        await call_judge(
            CFG,
            api_key="k",
            system_prompt="sys",
            user_prompt="user",
            output_type=Verdict,
            evaluator_name="answer_rubrics",
        )
    assert route.call_count == 1


async def test_a_parameter_list_error_is_not_retried(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions").mock(
        return_value=MockResponse(
            400,
            json={
                "error": {
                    "message": "Supported parameters: model, messages, max_tokens, "
                    "max_completion_tokens, reasoning_effort",
                    "param": "temperature",
                    "code": "invalid_request_error",
                }
            },
        )
    )
    with pytest.raises(httpx2.HTTPStatusError):
        await call_judge(
            CFG,
            api_key="k",
            system_prompt="sys",
            user_prompt="user",
            output_type=Verdict,
            evaluator_name="answer_rubrics",
        )
    assert route.call_count == 1


async def test_an_invalid_reasoning_effort_value_is_not_adapted(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions").mock(
        return_value=MockResponse(
            400,
            json={
                "error": {
                    "message": "Unsupported value: 'urgent' for 'reasoning_effort'. "
                    "Supported values are: 'low', 'medium' and 'high'.",
                    "param": "reasoning_effort",
                    "code": "invalid_value",
                }
            },
        )
    )
    with pytest.raises(httpx2.HTTPStatusError):
        await call_judge(
            CFG,
            api_key="k",
            system_prompt="sys",
            user_prompt="user",
            output_type=Verdict,
            evaluator_name="answer_rubrics",
        )
    assert route.call_count == 1


async def test_adapted_parameters_survive_a_json_repair_retry(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        MockResponse(
            400,
            json={
                "error": {
                    "message": "Unsupported parameter: 'max_tokens' is not supported with this "
                    "model. Use 'max_completion_tokens' instead.",
                    "param": "max_tokens",
                    "code": "unsupported_parameter",
                }
            },
        ),
        MockResponse(200, json=completion("not json at all")),
        MockResponse(200, json=completion('{"score": 0.4, "reason": "fixed"}')),
    ]
    verdict, _ = await call_judge(
        CFG,
        api_key="k",
        system_prompt="sys",
        user_prompt="user",
        output_type=Verdict,
        evaluator_name="answer_rubrics",
    )
    assert verdict.score == 0.4
    assert route.call_count == 3
    second = json.loads(route.calls[1].request.content)
    third = json.loads(route.calls[2].request.content)
    assert second["max_completion_tokens"] == 32768
    assert third["max_completion_tokens"] == 32768
    assert "max_tokens" not in third
    assert third["messages"][2] == {"role": "assistant", "content": "not json at all"}
    assert third["messages"][3]["role"] == "user"
    assert "corrected JSON object" in third["messages"][3]["content"]


async def test_second_failure_raises(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        MockResponse(200, json=completion("bad")),
        MockResponse(200, json=completion("still bad")),
    ]
    with pytest.raises(ValidationError):
        await call_judge(
            CFG,
            api_key="k",
            system_prompt="s",
            user_prompt="u",
            output_type=Verdict,
            session_id=None,
            evaluator_name="x",
        )
    assert route.call_count == 2


async def test_model_override(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions").mock(
        return_value=MockResponse(200, json=completion('{"score": 0.5, "reason": "ok"}'))
    )
    await call_judge(
        CFG,
        api_key="k",
        system_prompt="s",
        user_prompt="u",
        output_type=Verdict,
        evaluator_name="x",
        model="azure/gpt-5.2",
    )
    assert json.loads(route.calls[0].request.content)["model"] == "azure/gpt-5.2"


async def test_max_tokens_is_lowered_to_the_models_limit(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        too_large("max_tokens", 16384),
        MockResponse(200, json=completion('{"score": 0.9, "reason": "ok"}')),
    ]
    assert (await judge_once()).score == 0.9
    assert route.call_count == 2
    second = json.loads(route.calls[1].request.content)
    assert second["max_tokens"] == 16384
    assert second["reasoning_effort"] == "high"


async def test_max_completion_tokens_is_lowered_after_the_rename(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        MAX_TOKENS_REJECTED,
        too_large("max_completion_tokens", 8192),
        MockResponse(200, json=completion('{"score": 0.9, "reason": "ok"}')),
    ]
    assert (await judge_once()).score == 0.9
    third = json.loads(route.calls[2].request.content)
    assert third["max_completion_tokens"] == 8192
    assert "max_tokens" not in third


async def test_a_limit_above_the_setting_is_not_retried(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions").mock(
        return_value=too_large("max_tokens", 65536)
    )
    with pytest.raises(httpx2.HTTPStatusError):
        await judge_once()
    assert route.call_count == 1


async def test_later_calls_reuse_what_the_endpoint_rejected(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    ok = MockResponse(200, json=completion('{"score": 0.9, "reason": "ok"}'))
    route.side_effect = [
        MAX_TOKENS_REJECTED,
        too_large("max_completion_tokens", 16384),
        MockResponse(
            400,
            json={"error": {"message": "Unrecognized request argument supplied: reasoning_effort"}},
        ),
        ok,
        ok,
    ]
    await judge_once()
    await judge_once()
    assert route.call_count == 5
    last = json.loads(route.calls[4].request.content)
    assert last["max_completion_tokens"] == 16384
    assert "max_tokens" not in last
    assert "reasoning_effort" not in last


async def test_adaptations_are_kept_per_endpoint_and_model(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions")
    ok = MockResponse(200, json=completion('{"score": 0.9, "reason": "ok"}'))
    route.side_effect = [MAX_TOKENS_REJECTED, ok, ok]
    await judge_once(model="gpt-5-mini")
    await judge_once(model="gpt-4.1-mini")
    other = json.loads(route.calls[2].request.content)
    assert other["max_tokens"] == 32768
    assert "max_completion_tokens" not in other


async def test_an_empty_reasoning_effort_is_not_sent(httpx2_mock):
    route = httpx2_mock.post("https://judge.test/v1/chat/completions").mock(
        return_value=MockResponse(200, json=completion('{"score": 0.5, "reason": "ok"}'))
    )
    await judge_once(CFG.model_copy(update={"reasoning_effort": ""}))
    body = json.loads(route.calls[0].request.content)
    assert "reasoning_effort" not in body
    assert body["max_tokens"] == 32768
