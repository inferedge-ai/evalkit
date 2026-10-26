from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

ACCEPTED_SCHEMA_VERSIONS = frozenset({"ATIF-v1.8"})


class TrajectoryValidationError(Exception):
    pass


class AtifModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class AtifAgent(AtifModel):
    name: str
    version: str | None = None
    model_name: str | None = None
    tool_definitions: list[dict[str, Any]] | None = None
    extra: dict[str, Any] | None = None


class ContentPart(AtifModel):
    type: str
    text: str | None = None
    media_type: str | None = None
    path: str | None = None


class ToolCall(AtifModel):
    tool_call_id: str
    function_name: str
    arguments: dict[str, Any]
    extra: dict[str, Any] | None = None


class SubagentTrajectoryRef(AtifModel):
    trajectory_id: str | None = None
    trajectory_path: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] | None = None


class ObservationResult(AtifModel):
    source_call_id: str | None = None
    content: str | list[ContentPart] | None = None
    subagent_trajectory_ref: list[SubagentTrajectoryRef] | None = None
    extra: dict[str, Any] | None = None


class Observation(AtifModel):
    results: list[ObservationResult]


class StepMetrics(AtifModel):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    extra: dict[str, Any] | None = None


class FinalMetrics(AtifModel):
    total_prompt_tokens: int | None = None
    total_completion_tokens: int | None = None
    total_cached_tokens: int | None = None
    total_cost_usd: float | None = None
    total_steps: int | None = None


class Step(AtifModel):
    step_id: int
    timestamp: str | None = None
    source: Literal["user", "agent", "system"]
    message: str | list[ContentPart] | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] | None = None
    observation: Observation | None = None
    metrics: StepMetrics | None = None
    llm_call_count: int | None = None
    model_name: str | None = None


class Trajectory(AtifModel):
    schema_version: str
    session_id: str | None = None
    trajectory_id: str | None = None
    agent: AtifAgent | None = None
    steps: list[Step]
    final_metrics: FinalMetrics | None = None
    subagent_trajectories: list["Trajectory"] | None = None
    extra: dict[str, Any] | None = None


def validate_trajectory(data: dict) -> Trajectory:
    version = data.get("schema_version")
    if version not in ACCEPTED_SCHEMA_VERSIONS:
        raise TrajectoryValidationError(
            f"unsupported schema_version {version!r}; accepted: {sorted(ACCEPTED_SCHEMA_VERSIONS)}"
        )
    try:
        return Trajectory.model_validate(data)
    except ValidationError as exc:
        missing = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise TrajectoryValidationError(f"invalid ATIF document: {missing}") from exc


def subagent_link_warnings(trajectory: Trajectory) -> list[str]:
    missing = sum(1 for sub in trajectory.subagent_trajectories or [] if not sub.trajectory_id)
    if not missing:
        return []
    return [f"{missing} embedded subagent(s) have no trajectory_id (required since ATIF-v1.7)"]
