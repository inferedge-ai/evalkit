from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class JudgeSettings(BaseModel):
    """The LLM judge an experiment uses; exported from `evalkit.sdk` as `Judge`.

    Its API key comes from `credentials.judges` in `evalkit.yaml`, keyed by `base_url`.

    Attributes:
        base_url: An OpenAI-compatible endpoint that supports structured output.
        model: The model name at that endpoint.
        reasoning_effort: Passed to the model as `reasoning_effort`.
        max_tokens: The most tokens one judge reply may use.
    """

    model_config = ConfigDict(extra="forbid")

    base_url: str
    model: str
    reasoning_effort: str = "high"
    max_tokens: int = Field(32768, ge=1)

    @model_validator(mode="before")
    @classmethod
    def _drop_concurrency(cls, data: Any) -> Any:
        if isinstance(data, dict) and "concurrency" in data:
            return {k: v for k, v in data.items() if k != "concurrency"}
        return data


class AgentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_concurrent_runs: int = Field(default=4, ge=1)
    trajectory_wait_seconds: int = Field(default=60, ge=0)
    run_timeout_seconds: int = Field(default=840, ge=0)
    poll_interval_seconds: int = Field(default=5, ge=0)
    stop_grace_seconds: int = Field(default=120, ge=0)
    provides: dict[str, bool] | None = None
    input_schema: dict | None = None

    @field_validator("input_schema")
    @classmethod
    def _check_input_schema(cls, schema: dict | None) -> dict | None:
        if schema is not None:
            try:
                validator_for(schema).check_schema(schema)
            except SchemaError as exc:
                raise ValueError(f"input_schema is not a valid JSON Schema: {exc.message}") from exc
        return schema


class RemoteEvaluatorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoint: str
    timeout_seconds: int = Field(default=120, ge=1)
