import json

import httpx
import pytest
import respx
from pydantic import BaseModel, ValidationError

from evalkit.contracts.settings import JudgeSettings
from evalkit.core.judge import call_judge

CFG = JudgeSettings(
    base_url="https://judge.test/v1",
    model="vertex_ai/gemini-3.6-flash",
)


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


@respx.mock
async def test_request_shape_and_parse():
    route = respx.post("https://judge.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion('{"score": 0.8, "reason": "ok"}'))
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


@respx.mock
async def test_one_repair_retry():
    route = respx.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        httpx.Response(200, json=completion("not json at all")),
        httpx.Response(200, json=completion('{"score": 1.0, "reason": "fixed"}')),
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


@respx.mock
async def test_second_failure_raises():
    route = respx.post("https://judge.test/v1/chat/completions")
    route.side_effect = [
        httpx.Response(200, json=completion("bad")),
        httpx.Response(200, json=completion("still bad")),
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


@respx.mock
async def test_model_override():
    route = respx.post("https://judge.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=completion('{"score": 0.5, "reason": "ok"}'))
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
