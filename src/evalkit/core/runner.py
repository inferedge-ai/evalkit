"""The experiment runner: `prepare_experiment` pins the selection and creates
the record (server only; it needs the review state), then `drive_experiment`
(phase 1: task runs) and `evaluate_experiment` (phase 2: scoring) run
anywhere a `Store` and a `BlobStore` are available. Both phases are idempotent so
a failed experiment resumes instead of restarting.

Phase 1 fans agent runs out to the harness under a concurrency cap, persists
each trajectory and the run's artifacts to the blob store at completion, and
records the run. Phase 2 walks recorded runs and executes configured
built-ins, configured custom evaluators, and every gauge, publishing evaluation
rows to the store. The runner holds a lease on the experiment throughout and
renews it while it works.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Iterable
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import IO, Literal, Protocol

import httpx
from pydantic import BaseModel, ValidationError

from evalkit.config import RunnerConfig
from evalkit.contracts.atif import Trajectory, TrajectoryValidationError, validate_trajectory
from evalkit.contracts.custom_evaluator import EvalCase, ScoreItem
from evalkit.contracts.enums import (
    EvaluationSource,
    EvaluatorKind,
    ExperimentStatus,
    RunStatus,
    TrajectoryStatus,
)
from evalkit.contracts.harness import (
    Capabilities,
    FileRef,
    RunMetadata,
    answer_text,
    input_errors,
)
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.agents import AgentDefinition, AgentLookup, explicit
from evalkit.core.blob import (
    BlobStore,
    artifact_key,
    fixture_blob_key,
    trajectory_key,
    workspace_key,
)
from evalkit.core.capabilities import traced_capabilities
from evalkit.core.costmap import load_cost_map
from evalkit.core.custom_eval import EvalError, score_with_custom_evaluator
from evalkit.core.evaluators.base import (
    BUILTIN_EVALUATORS,
    BUILTIN_GAUGES,
    EvalContext,
    execute,
    neutral_skip,
)
from evalkit.core.harness import HarnessClient, RunTimeout
from evalkit.core.paths import is_safe_path
from evalkit.core.python_eval import PythonEvaluator, score_with_python_evaluator
from evalkit.core.remote_evaluators import EvaluatorLookup, RemoteEvaluator
from evalkit.core.traces.poll import poll_trajectory
from evalkit.core.traces.sessions import SessionReader
from evalkit.core.trajectory import (
    RunMetrics,
    TrajectoryView,
    backfill_step_costs,
    derive_metrics,
)
from evalkit.core.workspace import parse_slug
from evalkit.credentials import KeySource
from evalkit.store.base import (
    AgentRunRecord,
    CaseView,
    DatasetRow,
    EvaluationRow,
    Experiment,
    ExperimentConfig,
    ExperimentSnapshot,
    GitRevision,
    NewExperiment,
    PinnedVersion,
    RunForScoring,
    ScoreRow,
    Selection,
    Store,
)

LOGGER = logging.getLogger(__name__)

LEASE_SECONDS = 60
RENEW_EVERY_SECONDS = 15
ACTIVE = frozenset({ExperimentStatus.RUNNING, ExperimentStatus.EVALUATING})
ALLOWED: frozenset[tuple[ExperimentStatus | None, ExperimentStatus]] = frozenset(
    {
        (None, ExperimentStatus.RUNNING),
        (ExperimentStatus.RUNNING, ExperimentStatus.EVALUATING),
        (ExperimentStatus.EVALUATING, ExperimentStatus.COMPLETED),
        (ExperimentStatus.RUNNING, ExperimentStatus.FAILED),
        (ExperimentStatus.EVALUATING, ExperimentStatus.FAILED),
        (ExperimentStatus.FAILED, ExperimentStatus.RUNNING),
        (ExperimentStatus.COMPLETED, ExperimentStatus.RUNNING),
        (ExperimentStatus.COMPLETED, ExperimentStatus.EVALUATING),
        (ExperimentStatus.FAILED, ExperimentStatus.EVALUATING),
    }
)


class ExperimentRequest(BaseModel):
    dataset: str
    agent: str
    selection: Selection = Selection(all=True)
    repetitions: int = 1
    model: str | None = None
    capabilities: Capabilities | None = None
    cases: GitRevision | None = None
    evalkit_version: str | None = None
    code_evaluators: list[str] | None = None
    judge: JudgeSettings | None = None
    holder: str | None = None
    trajectory_wait_seconds: int | None = None


def _no_progress(message: str) -> None:
    return None


class AgentKeySource(Protocol):
    async def agent_key(self, agent: AgentDefinition) -> str | None: ...


@dataclass
class RunnerDeps:
    cfg: RunnerConfig
    store: Store
    blob_store: BlobStore
    agents: AgentLookup
    evaluators: EvaluatorLookup
    credentials: KeySource
    harness_transport: httpx.AsyncBaseTransport | None = None
    custom_eval_transport: httpx.AsyncBaseTransport | None = None
    progress: Callable[[str], None] = _no_progress
    sessions: SessionReader | None = None
    python_evaluators: dict[str, PythonEvaluator] = field(default_factory=dict)
    agent_keys: AgentKeySource | None = None


class ExperimentFactory(Protocol):
    async def dataset(self, ref: str) -> DatasetRow: ...

    async def cases_for(
        self, dataset: str, selection: Selection, *, agent: str | None
    ) -> list[CaseView]: ...

    async def create_experiment(self, req: NewExperiment) -> Experiment: ...


async def _spool_fixtures(
    deps: RunnerDeps, case: CaseView, stack: ExitStack
) -> list[tuple[str, IO[bytes]]]:
    files: list[tuple[str, IO[bytes]]] = []
    for file in case.files:
        spool = stack.enter_context(tempfile.TemporaryFile())
        blob = await deps.blob_store.get_stream(fixture_blob_key(file.sha256))
        async for chunk in blob.chunks:
            await asyncio.to_thread(spool.write, chunk)
        spool.seek(0)
        files.append((file.key, spool))
    return files


async def _fixture_refs(deps: RunnerDeps, case: CaseView, *, with_urls: bool) -> list[FileRef]:
    return [
        FileRef(
            name=file.key,
            url=await deps.blob_store.object_url(fixture_blob_key(file.sha256))
            if with_urls
            else None,
            context=file.context,
        )
        for file in case.files
    ]


class AgentArchived(ValueError):
    def __init__(self, ref: str):
        super().__init__(f"agent {ref} is archived")


class NothingRunnable(ValueError):
    def __init__(self, dataset: str, dropped: list[str]):
        reason = (
            "every selected case needs fixture files and the harness accepts none: "
            + ", ".join(dropped)
            if dropped
            else "no cases match the selection"
        )
        super().__init__(f"nothing to run in {dataset}: {reason}")


class InvalidCaseInputs(Exception):
    def __init__(self, agent: str, bad: dict[str, str]):
        self.agent = agent
        self.bad = bad
        lines = "\n".join(f"  {case_id}: {error}" for case_id, error in sorted(bad.items()))
        super().__init__(f"{len(bad)} case input(s) fail agent {agent}'s input_schema:\n{lines}")


class LeaseLost(ValueError):
    def __init__(self, exp_id: int):
        self.exp_id = exp_id
        super().__init__(
            f"experiment {exp_id} was taken over by another runner or marked failed; "
            f"check it with evalkit status {exp_id}, then resume it"
        )


def error_text(exc: BaseException) -> str:
    message = str(exc)
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


class ScoringFailed(Exception):
    def __init__(self, exp_id: int, failed: int, scored: int, first: BaseException):
        self.exp_id = exp_id
        super().__init__(
            f"scoring failed for {failed} of {failed + scored} runs of experiment {exp_id}; "
            f"first error: {error_text(first)}"
        )


def case_title(case_input: object) -> str | None:
    question = case_input.get("question") if isinstance(case_input, dict) else None
    if not isinstance(question, str) or not question.strip():
        return None
    return question.strip().splitlines()[0][:200]


async def _persist_artifacts(
    deps: RunnerDeps,
    client: HarnessClient,
    harness_run_id: str,
    refs: list[dict],
) -> None:
    for ref in refs:
        path = ref.get("path")
        if not path:
            continue
        if not is_safe_path(path):
            LOGGER.warning(
                "artifact %s for run %s has an unsafe path; skipped", path, harness_run_id
            )
            continue
        try:
            data = await client.get_file(harness_run_id, path)
        except httpx.HTTPError as exc:
            LOGGER.warning("artifact %s for run %s unavailable: %s", path, harness_run_id, exc)
            continue
        await deps.blob_store.put_object(artifact_key(client.agent.id, harness_run_id, path), data)


async def _persist_workspace_tree(
    deps: RunnerDeps, client: HarnessClient, harness_run_id: str
) -> None:
    try:
        tree = await client.list_files(harness_run_id)
    except httpx.HTTPError as exc:
        LOGGER.warning("workspace tree for run %s unavailable: %s", harness_run_id, exc)
        return
    await deps.blob_store.put_object(
        workspace_key(client.agent.id, harness_run_id), tree.model_dump_json().encode()
    )


@dataclass
class DrivenRun:
    case: CaseView
    repetition: int
    started: datetime
    harness_run_id: str
    answer: str | None = None
    output: dict | None = None
    error: str | None = None
    run_status: RunStatus = RunStatus.FAILED
    metrics: dict = field(default_factory=dict)
    reported_model: str | None = None
    trajectory: Trajectory | None = None
    trajectory_status: TrajectoryStatus | None = None
    wall_ms: int = 0


async def _drive_one(
    deps: RunnerDeps,
    exp: Experiment,
    client: HarnessClient,
    caps: Capabilities,
    case: CaseView,
    repetition: int,
    model: str | None,
) -> DrivenRun:
    started = datetime.now(UTC)
    clock = time.monotonic()
    file_refs: list[FileRef] | None = None
    if case.files:
        file_refs = await _fixture_refs(deps, case, with_urls=caps.accepts.file_urls)
    with ExitStack() as stack:
        files = (
            await _spool_fixtures(deps, case, stack)
            if case.files and not caps.accepts.file_urls
            else None
        )
        harness_run_id = await client.start_run(
            case.input,
            files=files,
            file_refs=file_refs,
            model=model,
            metadata=RunMetadata(
                experiment_id=str(exp.id), case_id=case.case_id, repetition=repetition
            ),
        )
    driven = DrivenRun(
        case=case, repetition=repetition, started=started, harness_run_id=harness_run_id
    )
    try:
        status = await client.poll_until_terminal(harness_run_id)
        driven.answer = status.answer
        driven.output = status.output
        driven.error = status.error
        driven.run_status = RunStatus(status.status)
        driven.reported_model = status.model
        if status.status != "completed" and driven.error is None:
            driven.error = f"run ended with status {status.status}"
        if status.artifacts:
            driven.metrics["artifacts"] = [
                a.model_dump(exclude_none=True) for a in status.artifacts
            ]
    except RunTimeout:
        driven.run_status = RunStatus.TIMEOUT
        driven.error = f"run timed out after {client.agent.settings.run_timeout_seconds}s"
    except ValidationError as exc:
        driven.error = f"harness protocol violation: {exc.errors()[0]['msg']}"
    driven.wall_ms = int((time.monotonic() - clock) * 1000)
    fetch = not client.agent.is_otel and caps.provides.trajectory
    if fetch and driven.error != "cancelled":
        try:
            driven.trajectory = await client.trajectory(harness_run_id)
            driven.trajectory_status = TrajectoryStatus.VALID
        except TrajectoryValidationError as exc:
            driven.trajectory_status = TrajectoryStatus.INVALID
            LOGGER.warning("trajectory for run %s failed ATIF validation: %s", harness_run_id, exc)
        except httpx.HTTPError as exc:
            driven.trajectory_status = TrajectoryStatus.UNAVAILABLE
            LOGGER.warning("trajectory fetch for run %s failed: %s", harness_run_id, exc)
    return driven


def reused_run_id(harness_run_id: str) -> str:
    return f"{harness_run_id}#reused-{uuid.uuid4().hex[:12]}"


async def store_trajectory(
    deps: RunnerDeps,
    agent_id: int,
    harness_run_id: str,
    trajectory: Trajectory,
    status: TrajectoryStatus | None,
    model: str | None,
) -> str:
    backfill_step_costs(trajectory, load_cost_map(deps.cfg.cost_map), model)
    if status == TrajectoryStatus.PARTIAL:
        trajectory.extra = {**(trajectory.extra or {}), "trajectory_status": "partial"}
    key = trajectory_key(agent_id, harness_run_id)
    await deps.blob_store.put_object(
        key, json.dumps(trajectory.model_dump(exclude_none=True)).encode()
    )
    return key


async def _record_one(
    deps: RunnerDeps,
    exp: Experiment,
    client: HarnessClient,
    caps: Capabilities,
    driven: DrivenRun,
    holder: str | None,
) -> str | None:
    agent = client.agent
    used = await deps.store.harness_run_id_used(exp.id, driven.harness_run_id)
    if used is not None:
        driven.harness_run_id = reused_run_id(driven.harness_run_id)
        driven.run_status = RunStatus.FAILED
        driven.error = used
        driven.trajectory = None
        driven.trajectory_status = TrajectoryStatus.UNAVAILABLE
        driven.metrics.pop("artifacts", None)
    elif agent.is_otel and driven.error != "cancelled":
        if deps.sessions is None:
            raise ValueError(
                f"agent {agent.name} takes its trajectory from traces but no session reader "
                "is configured"
            )
        doc, status = await poll_trajectory(
            deps.sessions,
            exp.agent,
            driven.harness_run_id,
            agent.settings.trajectory_wait_seconds,
        )
        driven.trajectory_status = TrajectoryStatus(status)
        if doc is not None:
            driven.trajectory = validate_trajectory(doc)
    key: str | None = None
    if driven.trajectory is not None:
        key = await store_trajectory(
            deps,
            agent.id,
            driven.harness_run_id,
            driven.trajectory,
            driven.trajectory_status,
            exp.config.model or driven.reported_model,
        )
        view = TrajectoryView(driven.trajectory)
        driven.metrics.update(derive_metrics(view, driven.wall_ms).model_dump(exclude_none=True))
    driven.metrics.setdefault("duration_ms", driven.wall_ms)
    await deps.store.record_run(
        exp.id,
        driven.case.case_id,
        driven.repetition,
        AgentRunRecord(
            harness_run_id=driven.harness_run_id,
            status=driven.run_status,
            answer=driven.answer,
            output=driven.output,
            error=driven.error,
            started_at=driven.started,
            ended_at=datetime.now(UTC),
            trajectory_key=key,
            trajectory_status=driven.trajectory_status,
            metrics=driven.metrics,
            title=case_title(driven.case.input),
        ),
        holder=holder,
    )
    deps.progress(f"run {driven.case.case_id} #{driven.repetition} {driven.run_status}")
    if caps.provides.workspace and driven.metrics.get("artifacts"):
        await _persist_artifacts(deps, client, driven.harness_run_id, driven.metrics["artifacts"])
    if caps.provides.workspace and used is None:
        await _persist_workspace_tree(deps, client, driven.harness_run_id)
    return driven.reported_model


def dropped_cases_text(dropped: list[str]) -> str:
    return (
        f"skipped {len(dropped)} case(s) with fixture files; the agent accepts neither "
        f"files nor file urls: {', '.join(dropped)}"
    )


def runnable(cases: list[CaseView], caps: Capabilities) -> tuple[list[CaseView], list[str]]:
    if caps.accepts.file_input:
        return cases, []
    gated = sorted(case.case_id for case in cases if case.files)
    return [case for case in cases if not case.files], gated


def _check_inputs(agent: str, caps: Capabilities, cases: list[CaseView]) -> None:
    bad = {}
    for case in cases:
        errors = input_errors(caps.effective_input_schema, case.input)
        if errors:
            bad[case.case_id] = errors[0]
    if bad:
        raise InvalidCaseInputs(agent, bad)


async def harness_capabilities(
    agent: AgentDefinition, api_key: str | None, transport: httpx.AsyncBaseTransport | None
) -> Capabilities:
    client = HarnessClient(agent, api_key, transport=transport)
    try:
        return traced_capabilities(await client.capabilities(), agent)
    finally:
        await client.aclose()


async def prepare_experiment(
    factory: ExperimentFactory,
    req: ExperimentRequest,
    *,
    agent: AgentDefinition,
    caps: Capabilities,
    remote_evaluators: dict[str, RemoteEvaluator],
    judge: JudgeSettings,
    runner: str,
    holder: str,
    revision: GitRevision | None = None,
) -> tuple[Experiment, list[str]]:
    model = req.model
    if model is not None and not caps.accepts.model_override:
        LOGGER.warning(
            "harness %s does not accept model override — running with its default model",
            req.agent,
        )
        model = None
    dataset = await factory.dataset(req.dataset)
    cases = await factory.cases_for(dataset.ref, req.selection, agent=agent.ref)
    cases, dropped = runnable(cases, caps)
    if not cases:
        raise NothingRunnable(dataset.ref, dropped)
    _check_inputs(req.agent, caps, cases)
    config = ExperimentConfig(
        model=model,
        judge=judge,
        selection=req.selection,
        revision=req.cases or revision,
        snapshot=ExperimentSnapshot(
            capabilities=caps.model_dump(),
            trajectory_source=agent.trajectory_source,
            trajectory_wait_seconds=agent.settings.trajectory_wait_seconds
            if req.trajectory_wait_seconds is None
            else req.trajectory_wait_seconds,
            remote_evaluators={
                name: evaluator.settings for name, evaluator in remote_evaluators.items()
            },
            code_evaluators=sorted(req.code_evaluators or []),
            evalkit_version=req.evalkit_version,
        ),
    )
    exp = await factory.create_experiment(
        NewExperiment(
            dataset=dataset.ref,
            agent=agent.ref,
            runner=runner,
            repetitions=req.repetitions,
            config=config,
            cases=[
                PinnedVersion(case_ref=case.case_ref, content_hash=case.content_hash)
                for case in cases
            ],
            holder=holder,
        )
    )
    return exp, dropped


async def _agent_for(deps: RunnerDeps, exp: Experiment) -> AgentDefinition:
    agent = await deps.agents.get(explicit(parse_slug(exp.agent)))
    if agent.archived:
        raise AgentArchived(agent.ref)
    return agent


async def drive_experiment(deps: RunnerDeps, exp_id: int, holder: str | None = None) -> None:
    exp = await deps.store.get_experiment(exp_id)
    agent = await _agent_for(deps, exp)
    client = HarnessClient(
        agent, deps.credentials.key_for("agent", agent.ref), transport=deps.harness_transport
    )
    # Runs execute under the experiment's snapshotted contract (capabilities,
    # model, pinned cases), never a fresh harness call, so resumed runs are
    # gated and modelled like the rest of the experiment.
    caps = Capabilities.model_validate(exp.config.snapshot.capabilities)
    model = exp.config.model
    try:
        cases = {case.case_id: case for case in await deps.store.pinned_cases(exp_id)}
        judge_key(deps, exp.config.judge, cases.values())
        limit = agent.settings.max_concurrent_runs
        if caps.limits.max_concurrent_runs is not None:
            limit = min(limit, caps.limits.max_concurrent_runs)
        semaphore = asyncio.Semaphore(max(limit, 1))
        reported_models: list[str | None] = []

        async def bounded(case: CaseView, repetition: int) -> None:
            async with semaphore:
                driven = await _drive_one(deps, exp, client, caps, case, repetition, model)
            reported_models.append(await _record_one(deps, exp, client, caps, driven, holder))

        await asyncio.gather(
            *(
                bounded(cases[pending.case_id], pending.repetition)
                for pending in await deps.store.pending_runs(exp_id)
            )
        )
        if model is None:
            observed = next((m for m in reported_models if m), None)
            if observed is not None:
                await deps.store.set_model(exp_id, observed)
    finally:
        await client.aclose()


async def _renew_forever(store: Store, exp_id: int, holder: str) -> None:
    renewed_at = time.monotonic()
    while True:
        await asyncio.sleep(RENEW_EVERY_SECONDS)
        try:
            renewed = await store.renew_lease(exp_id, holder)
        except Exception as exc:
            if time.monotonic() - renewed_at >= LEASE_SECONDS:
                raise LeaseLost(exp_id) from exc
            LOGGER.warning("renewing the lease on experiment %s failed: %s", exp_id, exc)
            continue
        if not renewed:
            LOGGER.warning("lost lease on experiment %s", exp_id)
            raise LeaseLost(exp_id)
        renewed_at = time.monotonic()


async def with_lease(store: Store, exp_id: int, holder: str, work: Awaitable[None]) -> None:
    renew = asyncio.create_task(_renew_forever(store, exp_id, holder))
    main = asyncio.ensure_future(work)
    try:
        await asyncio.wait({main, renew}, return_when=asyncio.FIRST_COMPLETED)
        if not main.done():
            main.cancel()
            await asyncio.gather(main, return_exceptions=True)
            renew.result()
        main.result()
    finally:
        renew.cancel()
        main.cancel()


async def run_leased(deps: RunnerDeps, exp_id: int, holder: str, work: Awaitable[None]) -> None:
    try:
        await with_lease(deps.store, exp_id, holder, work)
    except LeaseLost:
        raise
    except Exception as exc:
        await deps.store.transition(exp_id, ExperimentStatus.FAILED, holder, error=error_text(exc))
        raise
    await deps.store.transition(exp_id, ExperimentStatus.COMPLETED, holder)


async def run_experiment(deps: RunnerDeps, exp_id: int, holder: str) -> None:
    async def work() -> None:
        await drive_experiment(deps, exp_id, holder)
        await deps.store.transition(exp_id, ExperimentStatus.EVALUATING, holder)
        await evaluate_experiment(deps, exp_id, holder)

    await run_leased(deps, exp_id, holder, work())


async def rescore_experiment(
    deps: RunnerDeps, exp_id: int, holder: str, *, refetch_trajectories: bool = False
) -> None:
    await run_leased(
        deps,
        exp_id,
        holder,
        evaluate_experiment(deps, exp_id, holder, refetch_trajectories=refetch_trajectories),
    )


def judge_key_for(
    credentials: KeySource, judge: JudgeSettings, cases: Iterable[CaseView]
) -> str | None:
    needed = any(
        BUILTIN_EVALUATORS[name].uses_judge
        for case in cases
        for name in _configured_builtins(case.effective_evaluators)
    )
    return credentials.require_judge_key(judge.base_url) if needed else None


def judge_key(deps: RunnerDeps, judge: JudgeSettings, cases: Iterable[CaseView]) -> str | None:
    return judge_key_for(deps.credentials, judge, cases)


def _configured_builtins(evaluators: dict) -> dict[str, dict]:
    configured: dict[str, dict] = {}
    for family in ("output", "trajectory"):
        for name, config in (evaluators.get(family) or {}).items():
            if name in BUILTIN_EVALUATORS:
                configured[name] = config
    return configured


REFETCHABLE = (TrajectoryStatus.PARTIAL, TrajectoryStatus.UNAVAILABLE)
REFETCH_CONCURRENCY = 8
SCORABLE = (TrajectoryStatus.VALID, TrajectoryStatus.PARTIAL)


async def stored_trajectory_doc(
    blob_store: BlobStore, agent_id: int, run: AgentRunRecord
) -> tuple[dict | None, TrajectoryStatus | None]:
    status = run.trajectory_status
    if status in REFETCHABLE:
        candidate = trajectory_key(agent_id, run.harness_run_id)
        if await blob_store.object_exists(candidate):
            doc = json.loads(await blob_store.get_object(candidate))
            return doc, TrajectoryStatus((doc.get("extra") or {}).get("trajectory_status", "valid"))
    if run.trajectory_key and status in SCORABLE:
        return json.loads(await blob_store.get_object(run.trajectory_key)), status
    return None, status


async def _load_view(deps: RunnerDeps, agent_id: int, run: AgentRunRecord) -> TrajectoryView | None:
    doc, status = await stored_trajectory_doc(deps.blob_store, agent_id, run)
    if doc is None or status not in SCORABLE:
        return None
    return TrajectoryView(validate_trajectory(doc))


def _rows(
    producer: str, source: EvaluationSource, scores: Iterable[ScoreRow]
) -> list[EvaluationRow]:
    return [
        EvaluationRow(
            producer=producer,
            name=score.name,
            kind=EvaluatorKind.GAUGE if score.kind == "gauge" else EvaluatorKind.SCORE,
            source=source,
            value=score.score,
            label=score.label,
            explanation=score.explanation,
            error=score.error,
        )
        for score in scores
    ]


def _custom_rows(
    name: str,
    kind: Literal["evaluator", "gauge"],
    source: EvaluationSource,
    outcome: list[ScoreItem] | EvalError,
) -> list[EvaluationRow]:
    if isinstance(outcome, EvalError):
        return _rows(
            name,
            source,
            [ScoreRow(name=name, kind=kind, score=None, label="error", error=outcome.message)],
        )
    return _rows(
        name,
        source,
        [
            ScoreRow(
                name=item.name,
                kind=kind,
                score=item.score,
                label=item.label,
                explanation=item.explanation,
            )
            for item in outcome
        ],
    )


def custom_case_input(case: CaseView) -> dict:
    if not case.files:
        return dict(case.input)
    return {**case.input, "files": [file.model_dump() for file in case.files]}


async def _evaluate_run(
    deps: RunnerDeps,
    exp: Experiment,
    agent: AgentDefinition,
    caps: Capabilities,
    cost_map: dict,
    run: RunForScoring,
    judge_semaphore: asyncio.Semaphore,
    judge_api_key: str | None,
    configured_custom: dict[str, RemoteEvaluator],
    holder: str | None,
    *,
    force: bool = False,
) -> None:
    record = run.agent_run
    structured = record.output
    answer = (
        answer_text(record.answer, structured) if structured is not None else record.answer or ""
    )
    metrics_dict = dict(record.metrics)
    view = await _load_view(deps, agent.id, record)
    if view is not None:
        wall = metrics_dict.get("duration_ms")
        metrics_dict = {**metrics_dict, **derive_metrics(view, wall).model_dump(exclude_none=True)}
    ctx = EvalContext(
        case=run.case,
        answer=answer,
        output=structured,
        trajectory_view=view,
        wall_ms=metrics_dict.get("duration_ms") or 0,
        judge_cfg=exp.config.judge,
        judge_api_key=judge_api_key,
        metrics=RunMetrics.model_validate(metrics_dict) if metrics_dict else None,
        session_id=record.harness_run_id,
        cost_map=cost_map,
    )
    already = set() if force else run.done
    rows: list[EvaluationRow] = []
    evaluators = run.case.effective_evaluators

    for name, config in _configured_builtins(evaluators).items():
        evaluator = BUILTIN_EVALUATORS[name]
        if set(evaluator.emits) <= already:
            continue
        async with judge_semaphore:
            try:
                scores = await execute(evaluator, ctx, config, caps)
            except Exception as exc:
                LOGGER.warning(
                    "evaluator %s errored on run %s: %s", name, run.experiment_run_id, exc
                )
                scores = [ScoreRow(name=name, score=None, label="error", error=str(exc))]
        rows.extend(_rows(name, EvaluationSource.BUILTIN, scores))

    custom_config = evaluators.get("custom") or {}
    trajectory_doc: dict = {}
    if view is not None:
        trajectory_doc = view.trajectory.model_dump(exclude_none=True)
    eval_case = EvalCase(
        id=run.case.case_id,
        input=custom_case_input(run.case),
        expected=run.case.expected,
        config={},
    )
    for name, config in custom_config.items():
        if name in already:
            continue
        python_evaluator = deps.python_evaluators.get(name)
        if python_evaluator is not None:
            if python_evaluator.requires_trajectory and view is None:
                rows.extend(
                    _rows(
                        name, EvaluationSource.CODE, [neutral_skip(name, "run has no trajectory")]
                    )
                )
                continue
            outcome = await score_with_python_evaluator(
                python_evaluator,
                case=eval_case.model_copy(update={"config": config}),
                answer=answer,
                output=structured,
                trajectory=trajectory_doc,
            )
            rows.extend(_custom_rows(name, python_evaluator.kind, EvaluationSource.CODE, outcome))
            continue
        evaluator_cfg = configured_custom.get(name)
        if evaluator_cfg is None:
            rows.extend(
                _rows(
                    name,
                    EvaluationSource.REMOTE,
                    [
                        ScoreRow(
                            name=name,
                            score=None,
                            label="error",
                            error=f"custom evaluator {name} is not configured",
                        )
                    ],
                )
            )
            continue
        result = await score_with_custom_evaluator(
            evaluator_cfg,
            deps.credentials.key_for("evaluator", evaluator_cfg.ref),
            case=eval_case.model_copy(update={"config": config}),
            answer=answer,
            output=structured,
            trajectory=trajectory_doc,
            transport=deps.custom_eval_transport,
        )
        rows.extend(_custom_rows(name, evaluator_cfg.wire_kind, EvaluationSource.REMOTE, result))

    for name, gauge in BUILTIN_GAUGES.items():
        if name in already:
            continue
        rows.extend(_rows(name, EvaluationSource.BUILTIN, await execute(gauge, ctx, {}, caps)))

    if rows:
        await deps.store.publish_evaluations(
            run.experiment_run_id, rows, force=force, holder=holder
        )
    deps.progress(f"scored {run.case.case_id} #{run.repetition} ({len(rows)} rows)")


async def _refetch(
    deps: RunnerDeps, exp: Experiment, agent: AgentDefinition, runs: list[RunForScoring]
) -> set[int]:
    if deps.sessions is None:
        raise ValueError("refetching trajectories needs a session reader")
    wait = exp.config.snapshot.trajectory_wait_seconds
    refetched: set[int] = set()
    slots = asyncio.Semaphore(REFETCH_CONCURRENCY)
    sessions = deps.sessions

    async def one(run: RunForScoring) -> None:
        async with slots:
            doc, status = await poll_trajectory(
                sessions, exp.agent, run.agent_run.harness_run_id, wait
            )
        if doc is None:
            return
        trajectory = validate_trajectory(doc)
        trajectory.extra = {**(trajectory.extra or {}), "trajectory_status": status}
        await store_trajectory(
            deps,
            agent.id,
            run.agent_run.harness_run_id,
            trajectory,
            TrajectoryStatus(status),
            exp.config.model,
        )
        refetched.add(run.experiment_run_id)
        deps.progress(f"refetched trajectory for {run.case.case_id} #{run.repetition}: {status}")

    await asyncio.gather(
        *(one(run) for run in runs if run.agent_run.trajectory_status in REFETCHABLE)
    )
    return refetched


async def evaluate_experiment(
    deps: RunnerDeps,
    exp_id: int,
    holder: str | None = None,
    *,
    refetch_trajectories: bool = False,
) -> None:
    exp = await deps.store.get_experiment(exp_id)
    agent = await deps.agents.get(explicit(parse_slug(exp.agent)))
    caps = Capabilities.model_validate(exp.config.snapshot.capabilities)
    cost_map = load_cost_map(deps.cfg.cost_map)
    runs = await deps.store.runs_to_score(exp_id, force=False)
    refetched = await _refetch(deps, exp, agent, runs) if refetch_trajectories else set()
    judge_semaphore = asyncio.Semaphore(exp.config.judge.concurrency)
    api_key = judge_key(deps, exp.config.judge, [run.case for run in runs])
    configured_custom = (
        await deps.evaluators.in_workspace(agent.workspace)
        if any(run.case.effective_evaluators.get("custom") for run in runs)
        else {}
    )
    results = await asyncio.gather(
        *(
            _evaluate_run(
                deps,
                exp,
                agent,
                caps,
                cost_map,
                run,
                judge_semaphore,
                api_key,
                configured_custom,
                holder,
                force=run.experiment_run_id in refetched,
            )
            for run in runs
        ),
        return_exceptions=True,
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    for error in errors:
        if not isinstance(error, Exception):
            raise error
    if errors:
        first = errors[0]
        raise ScoringFailed(exp_id, len(errors), len(results) - len(errors), first) from first
