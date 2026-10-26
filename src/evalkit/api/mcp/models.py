from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from evalkit.core.run_digest import (
    GroupStats,
    ModelCost,
    RunDigest,
    ToolCallGroup,
    ToolErrors,
    Turn,
)

NoteCode = Literal["not_found", "partial_coverage", "not_applicable"]


# An optional field with no value is left out rather than
# sent as null. A required field that can be null (`total`, a run's `cost`) stays.
class Out(BaseModel):
    @model_serializer(mode="wrap")
    def _drop_unset_optionals(self, handler: SerializerFunctionWrapHandler):
        out = handler(self)
        fields = type(self).model_fields
        return {
            k: v
            for k, v in out.items()
            if v is not None or (k in fields and fields[k].is_required())
        }


class Note(Out):
    code: NoteCode
    message: str | None = None


class Money(Out):
    value: float
    unit: Literal["currency"] = "currency"
    currency: Literal["USD"] = "USD"


def usd(value: float | None) -> Money | None:
    return Money(value=value) if value is not None else None


class PlainMeta(Out):
    count: int


class PagedMeta(Out):
    count: int
    total: int | None
    has_more: bool
    next_offset: int | None = None
    note: Note | None = None


class BatchMeta(Out):
    count: int
    matched: int
    unmatched: int
    failed: int | None = None


class AgentRow(Out):
    id: str
    name: str
    workspace: str
    workspace_type: Literal["shared", "personal"]
    trajectory_source: str
    driver: str
    archived: bool


class FindAgentsResponse(Out):
    data: list[AgentRow]
    meta: PlainMeta


class ToolErrorCount(Out, ToolErrors):
    pass


# The MCP rows are core's digest models with cost as a currency amount.
class ModelCostRow(Out, ModelCost):
    cost_usd: float = Field(default=0.0, exclude=True)
    cost: Money


class ToolCallRow(Out, ToolCallGroup):
    pass


class RunMetrics(Out, RunDigest):
    run_id: str = Field(default="", exclude=True)
    cost_usd: float | None = Field(default=None, exclude=True)
    tool_call_groups: list[ToolCallGroup] = Field(default=[], exclude=True)
    cost: Money | None
    tool_errors_by_tool: list[ToolErrorCount]  # type: ignore[assignment]
    cost_by_model: list[ModelCostRow]  # type: ignore[assignment]


class TurnRow(Out, Turn):
    cost_usd: float | None = Field(default=None, exclude=True)
    cost: Money | None


class RunRow(Out):
    id: str
    title: str | None = None
    created_at: datetime | None = None
    user_id: str | None = None
    client_id: str | None = None
    source: Literal["app", "eval"] | None = None
    status: str | None = None
    experiment_id: int | None = None
    experiment_number: int | None = None
    case_id: str | None = None
    metrics: RunMetrics | None = None
    turns: list[TurnRow] | None = None
    tool_calls: list[ToolCallRow] | None = None
    note: Note | None = None
    # A run whose trajectory could not be read: a failure, counted in meta.failed.
    error: str | None = None
    detail: str | None = None


class FindRunsResponse(Out):
    data: list[RunRow]
    meta: PagedMeta


class GetRunsResponse(Out):
    data: list[RunRow]
    meta: BatchMeta


class StatsRow(Out, GroupStats):
    cost_usd: float = Field(default=0.0, exclude=True)
    cost: Money


class StatsMeta(Out):
    count: int
    group_by: list[str]
    timezone: str
    runs_considered: int
    runs_in_window: int
    capped: bool
    unavailable: int
    note: Note | None = None


class GetRunStatsResponse(Out):
    data: list[StatsRow]
    meta: StatsMeta


class SubagentRef(Out):
    index: int
    trajectory_id: str | None
    agent: str | None
    steps: int


class TrajectoryMeta(PagedMeta):
    schema_version: str
    session_id: str | None
    trajectory_id: str | None
    agent: dict[str, Any] | None
    final_metrics: dict[str, Any] | None
    subagents: list[SubagentRef]


class GetTrajectoryResponse(Out):
    data: list[dict[str, Any]]
    meta: TrajectoryMeta


class FileRow(Out):
    path: str
    size: int
    content_type: str | None = None


class GetRunFilesResponse(Out):
    data: list[FileRow]
    meta: PlainMeta


class FileText(Out):
    path: str
    size: int
    text: str | None = None
    note: Note | None = None


class DownloadRunFileResponse(Out):
    data: list[FileText]
    meta: PagedMeta


class ExperimentRow(Out):
    model_config = ConfigDict(extra="allow")

    id: int
    number: int | None = None
    workspace: str | None = None
    dataset: str | None = None
    agent: str | None = None
    created_by: str | None = None
    status: str | None = None
    status_error: str | None = None
    repetitions: int | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    note: Note | None = None


class FindExperimentsResponse(Out):
    data: list[ExperimentRow]
    meta: PagedMeta


class GetExperimentsResponse(Out):
    data: list[ExperimentRow]
    meta: BatchMeta


class Evaluation(Out):
    name: str
    kind: str
    value: float | None
    label: str | None
    explanation: str | None
    error: str | None


class ExperimentRunRow(Out):
    id: str
    case_id: str
    repetition: int
    status: str
    error: str | None
    answer: str | None
    started_at: datetime
    ended_at: datetime | None
    metrics: dict[str, Any]
    evaluations: list[Evaluation]


class ExperimentRunsMeta(PagedMeta):
    agent: str


class GetExperimentRunsResponse(Out):
    data: list[ExperimentRunRow]
    meta: ExperimentRunsMeta
