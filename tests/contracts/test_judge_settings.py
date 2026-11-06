import pytest
from pydantic import ValidationError

from evalkit.contracts.settings import JudgeSettings


def test_provider_judge():
    judge = JudgeSettings(provider="openai", model="gpt-5")
    assert judge.base_url is None


def test_base_url_judge():
    judge = JudgeSettings(base_url="https://llm.example/v1", model="m")
    assert judge.provider is None


@pytest.mark.parametrize(
    "fields",
    [{}, {"provider": "openai", "base_url": "https://llm.example/v1"}],
)
def test_refuses_both_or_neither(fields):
    with pytest.raises(ValidationError, match="exactly one of provider or base_url"):
        JudgeSettings(model="m", **fields)


def test_the_api_key_is_never_serialized_or_shown():
    judge = JudgeSettings(base_url="https://llm.example/v1", model="m", api_key="sk-secret")
    assert judge.api_key == "sk-secret"
    assert "api_key" not in judge.model_dump()
    assert "sk-secret" not in judge.model_dump_json()
    assert "sk-secret" not in repr(judge)


def test_a_provider_judge_takes_no_api_key():
    with pytest.raises(ValidationError, match="stored key"):
        JudgeSettings(provider="openai", model="m", api_key="k")
