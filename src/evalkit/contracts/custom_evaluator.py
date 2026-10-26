from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CustomEvaluatorModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class EvalCase(CustomEvaluatorModel):
    id: str = Field(description="Case id.")
    input: dict = Field(description="Case input, verbatim.")
    expected: dict | None = Field(default=None, description="Case expected block, if any.")
    config: dict = Field(
        description="This case's YAML config for this evaluator. Opaque to evalkit — "
        "passed through untouched."
    )


class EvalRun(CustomEvaluatorModel):
    answer: str = Field(
        description="The agent's final answer. When the harness sent only a structured "
        "output, this is that output as canonical JSON (sorted keys)."
    )
    output: dict | None = Field(
        default=None,
        description="The agent's structured output, parsed, or null when the harness sent none.",
    )
    trajectory: dict = Field(description="The persisted ATIF trajectory document.")


class EvalRequest(CustomEvaluatorModel):
    schema_version: str = Field(default="v1", description="Custom Evaluator API version.")
    evaluator: str = Field(description="The registered name being invoked.")
    kind: Literal["evaluator", "gauge"] = Field(
        description="'evaluator' (scores) or 'gauge' (measures)."
    )
    case: EvalCase
    run: EvalRun


class ScoreItem(CustomEvaluatorModel):
    name: str = Field(
        description="Score name. Multi-score is first-class: one call may return "
        "several named scores."
    )
    score: float | None = Field(
        default=None,
        description="null means 'no reading' — recorded but excluded from aggregates.",
    )
    label: str | None = Field(default=None, description="Optional short label.")
    explanation: str | None = Field(
        default=None, description="Optional. Shown in the UI and EDD bundles."
    )


class EvalResponse(CustomEvaluatorModel):
    scores: list[ScoreItem] = Field(description="Named scores produced by this evaluator.")
