from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from evalkit.contracts.enums import (
    EvaluationSource,
    EvaluatorKind,
    ExperimentStatus,
    RunStatus,
    TrajectorySource,
    TrajectoryStatus,
)
from evalkit.contracts.settings import JudgeSettings, RemoteEvaluatorSettings


@dataclass(frozen=True)
class BlobStream:
    size: int
    chunks: AsyncIterator[bytes]


class UnknownDataset(KeyError):
    def __init__(self, ref: str):
        super().__init__(ref)
        self.ref = ref

    def __str__(self) -> str:
        return f"unknown dataset: {self.ref}"


class CaseFile(BaseModel):
    key: str
    sha256: str
    context: str


class FixtureUse(BaseModel):
    key: str
    sha256: str
    size: int
    datasets: list[str]
    uploaded_by: str | None


class FixtureUsePage(BaseModel):
    items: list[FixtureUse]
    total: int


class CaseView(BaseModel):
    case_ref: int
    case_id: str
    content_hash: str
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    evaluators: dict[str, Any] = Field(default_factory=dict)
    effective_evaluators: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    split_tags: list[str] = Field(default_factory=list)
    source: str | None = None
    files: list[CaseFile] = Field(default_factory=list)
    review_state: str | None = None


class DatasetSyncView(BaseModel):
    id: int
    source: str
    detail: dict[str, Any]
    actor: str
    created_at: datetime


class DatasetRow(BaseModel):
    id: int
    workspace: str
    workspace_is_shared: bool
    name: str
    kind: str
    last_sync: DatasetSyncView | None = None

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.workspace}"


class Selection(BaseModel):
    all: bool = False
    tag: str | None = None
    case_ids: list[str] | None = None


class IncomingCase(BaseModel):
    case_id: str
    content_hash: str
    input: dict[str, Any]
    expected: dict[str, Any] | None = None
    evaluators: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    path_tags: list[str] = Field(default_factory=list)
    source: str | None = None
    files: list[CaseFile] = Field(default_factory=list)


class IncomingOverlay(BaseModel):
    target_case_id: str
    agents: list[str]
    tags: list[str]
    evaluators: dict[str, Any]
    source_file: str | None = None


class CarriedReview(BaseModel):
    case_id: str
    reviewer: str


class SyncResult(BaseModel):
    sync_id: int
    dataset_id: int
    created: list[str] = Field(default_factory=list)
    patched: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    unchanged: int = 0
    overlays: int = 0
    carried: list[CarriedReview] = Field(default_factory=list)
    now_draft: list[str] = Field(default_factory=list)


class GitRevision(BaseModel):
    sha: str | None = None
    branch: str | None = None
    dirty: bool | None = None


class ExperimentSnapshot(BaseModel):
    capabilities: dict[str, Any]
    trajectory_source: TrajectorySource
    trajectory_wait_seconds: int
    remote_evaluators: dict[str, RemoteEvaluatorSettings] = Field(default_factory=dict)
    code_evaluators: list[str] = Field(default_factory=list)
    evalkit_version: str | None = None


class ExperimentConfig(BaseModel):
    model: str | None = None
    judge: JudgeSettings
    selection: Selection
    include_drafts: bool = False
    review_scope: Literal["approved", "all"] | None = None
    revision: GitRevision | None = None
    snapshot: ExperimentSnapshot


class Experiment(BaseModel):
    id: int
    number: int
    workspace: str
    dataset: str
    agent: str
    agent_id: int
    created_by: str
    status: ExperimentStatus
    status_error: str | None
    lease_holder: str | None
    lease_expires_at: datetime | None
    repetitions: int
    config: ExperimentConfig
    started_at: datetime
    ended_at: datetime | None


class PinnedVersion(BaseModel):
    case_ref: int
    content_hash: str


class NewExperiment(BaseModel):
    dataset: str
    agent: str
    runner: str
    repetitions: int
    config: ExperimentConfig
    cases: list[PinnedVersion]
    holder: str


class AgentRunRecord(BaseModel):
    harness_run_id: str
    status: RunStatus
    answer: str | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    trajectory_key: str | None = None
    trajectory_status: TrajectoryStatus | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    title: str | None = None


class RunRef(BaseModel):
    experiment_run_id: int
    agent_run_id: int


class CaseRep(BaseModel):
    case_id: str
    repetition: int


class EvaluationRow(BaseModel):
    producer: str
    name: str
    kind: EvaluatorKind
    source: EvaluationSource
    value: float | None = None
    label: str | None = None
    explanation: str | None = None
    error: str | None = None


class RunEvalContext(BaseModel):
    experiment_id: int
    repetition: int
    case: CaseView
    evaluations: list[EvaluationRow]


class RunForScoring(BaseModel):
    experiment_run_id: int
    case: CaseView
    repetition: int
    agent_run: AgentRunRecord
    agent_run_id: int
    done: set[str]


class RunDetail(BaseModel):
    experiment_run_id: int
    case_id: str
    repetition: int
    agent_run_id: int
    agent_run: AgentRunRecord
    evaluations: list[EvaluationRow] = Field(default_factory=list)


class AggregateRows(BaseModel):
    experiment: Experiment
    case_scores: list[tuple[str, str, float, str | None, str | None]]
    case_gauges: list[tuple[str, str, float]]
    case_errors: list[tuple[str, str, str]]
    run_errors: list[tuple[str, str]]
    repetitions_seen: dict[str, int]
    sources: dict[str, str]


RunState = Literal["pending", "ran", "evaluated"]


class Progress(BaseModel):
    total: int
    ran: int
    evaluated: int


class HistoryPoint(BaseModel):
    experiment_id: int
    number: int
    agent: str
    model: str | None = None
    created_by: str
    created_at: datetime
    repetitions: int
    scores: dict[str, float | None]
    cost_usd: float | None = None


class UnknownExperiment(KeyError):
    def __init__(self, experiment_id: int):
        super().__init__(f"unknown experiment: {experiment_id}")


class Conflict(Exception):
    pass


class InvalidRun(ValueError):
    pass


class ScoreRow(BaseModel):
    name: str
    kind: Literal["evaluator", "gauge"] = "evaluator"
    score: float | None = None
    label: str | None = None
    explanation: str | None = None
    source: str = "builtin"
    error: str | None = None
    annotator_kind: Literal["CODE", "LLM", "HUMAN"] = "CODE"


class Store(Protocol):
    async def cases_for(
        self, dataset: str, selection: Selection, *, agent: str | None, approved_only: bool
    ) -> list[CaseView]: ...

    async def get_experiment(self, exp_id: int) -> Experiment: ...

    async def pinned_cases(self, exp_id: int) -> list[CaseView]: ...

    async def transition(
        self, exp_id: int, to: ExperimentStatus, holder: str | None, error: str | None = None
    ) -> Experiment: ...

    async def renew_lease(self, exp_id: int, holder: str) -> bool: ...

    async def set_model(self, exp_id: int, model: str) -> None: ...

    async def pending_runs(self, exp_id: int) -> list[CaseRep]: ...

    async def harness_run_id_used(self, exp_id: int, harness_run_id: str) -> str | None: ...

    async def record_run(
        self,
        exp_id: int,
        case_id: str,
        repetition: int,
        run: AgentRunRecord,
        *,
        holder: str | None = None,
    ) -> RunRef: ...

    async def runs_to_score(self, exp_id: int, *, force: bool) -> list[RunForScoring]: ...

    async def publish_evaluations(
        self,
        experiment_run_id: int,
        rows: list[EvaluationRow],
        *,
        force: bool,
        holder: str | None = None,
    ) -> None: ...

    async def aclose(self) -> None: ...


def select_cases(cases: list[CaseView], selection: Selection) -> list[CaseView]:
    if selection.all:
        return list(cases)
    if selection.tag:
        return [case for case in cases if selection.tag in case.split_tags]
    wanted = set(selection.case_ids or [])
    return [case for case in cases if case.case_id in wanted]
