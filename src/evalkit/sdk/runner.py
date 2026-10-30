from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from opentelemetry import trace
from opentelemetry.context import Context
from pydantic import BaseModel

from evalkit import version
from evalkit.client import connection
from evalkit.client.agents import HttpAgents
from evalkit.client.api import ApiRequestError, AsyncApi, SyncApi, encode
from evalkit.client.blob import HttpBlobStore
from evalkit.client.evaluators import HttpEvaluators
from evalkit.client.sessions import HttpSessionReader
from evalkit.client.store import HttpStore, local_holder
from evalkit.client.summary import summary_lines
from evalkit.client.sync import git_revision, resolve_dataset, sync_dataset
from evalkit.config import LocalConfig
from evalkit.contracts.atif import TrajectoryValidationError, validate_trajectory
from evalkit.contracts.enums import ExperimentStatus, RunStatus, TrajectoryStatus
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.agents import AgentDefinition, explicit
from evalkit.core.blob import trajectory_key
from evalkit.core.capabilities import sdk_capabilities
from evalkit.core.costmap import load_cost_map
from evalkit.core.python_eval import PythonEvaluator
from evalkit.core.runner import (
    ExperimentRequest,
    LeaseLost,
    RunnerDeps,
    ScoringFailed,
    case_title,
    dropped_cases_text,
    evaluate_experiment,
    judge_key,
    judge_key_for,
    missing_local_keys,
    rescore_experiment,
    run_leased,
)
from evalkit.core.traces.mapping import SDK_ROOT_ATTRIBUTE, TASK_SESSION_ATTRIBUTE
from evalkit.core.traces.poll import poll_trajectory
from evalkit.core.traces.sessions import SessionReader
from evalkit.core.trajectory import TrajectoryView, backfill_step_costs, derive_metrics
from evalkit.core.workspace import Reference, parse_reference, parse_slug, slug
from evalkit.credentials import CredentialRegistry
from evalkit.sdk.binding import (
    Fixture,
    TaskSpec,
    arguments,
    call_task,
    ignores_model,
    inspect_task,
)
from evalkit.sdk.tracing import CURRENT_RUN
from evalkit.store.base import AgentRunRecord, CaseView, Experiment, Selection

ROOT_SPAN_NAME = "evalkit.run"
NO_PROVIDER_ERROR = "no tracer provider configured and the task returned no session_id"
Task = Callable[..., Any]
Progress = Callable[[str], None]


class RunResult(BaseModel):
    """What a task can return instead of a plain answer.

    Attributes:
        answer: The agent's answer text.
        output: The agent's structured output.
        model: The model the agent used, recorded on the run.
        error: An error message; the run is recorded as failed.
        session_id: The agent's own session id, when its spans do not continue the
            SDK's trace. The SDK reads the trajectory from that session.
        trajectory: An ATIF document to store as the trajectory, instead of building
            one from spans.
    """

    answer: str | None = None
    output: dict[str, Any] | None = None
    model: str | None = None
    error: str | None = None
    session_id: str | None = None
    trajectory: dict[str, Any] | None = None


class RunSummary(BaseModel):
    """One run of an experiment.

    Attributes:
        case_id: The case.
        repetition: 1 for the first repetition, 2 for the second, and so on.
        status: `completed`, `failed`, `cancelled` or `timeout`.
        trajectory_status: `valid`, `partial`, `invalid` or `unavailable`, or `None`
            when no trajectory was read.
        session_id: The session the run's spans belong to.
    """

    case_id: str
    repetition: int
    status: str
    trajectory_status: str | None
    session_id: str | None


class ExperimentResult(BaseModel):
    """The result of `run_experiment` or `evaluate_experiment`.

    Attributes:
        experiment_id: The experiment, or `None` after a dry run.
        url: The experiment's page in the web UI, or `None` after a dry run.
        runs: One entry per case and repetition.
    """

    experiment_id: int | None
    url: str | None
    runs: list[RunSummary]


class Outcome(BaseModel):
    run_id: str
    started: datetime
    answer: str | None = None
    output: dict[str, Any] | None = None
    model: str | None = None
    error: str | None = None
    status: str = "failed"
    session_id: str | None = None
    trajectory: dict[str, Any] | None = None
    wall_ms: int = 0


def _config_search_starts() -> list[Path]:
    script = getattr(sys.modules["__main__"], "__file__", None)
    starts = [Path.cwd()]
    if script is not None and Path(script).parent.resolve() != Path.cwd().resolve():
        starts.append(Path(script).parent)
    return starts


def _local_config() -> LocalConfig:
    starts = _config_search_starts()
    for start in starts:
        local = LocalConfig.find(start)
        if local is not None:
            return local
    raise FileNotFoundError(
        f"evalkit.yaml not found above {' or '.join(str(s) for s in starts)}; "
        "it holds your cases, fixtures and credentials"
    )


async def _agent_definition(api: AsyncApi, agent: str, me: str) -> AgentDefinition:
    definition = await HttpAgents(api).get(explicit(parse_reference(agent, me)))
    if definition.harness_url is not None:
        raise ValueError(
            f"{definition.ref} has a harness_url, so evalkit drives it through its Harness API: "
            f"run it with `evalkit run <dataset> --agent {agent}`"
        )
    return definition


def _credentials(local: LocalConfig, me: str) -> CredentialRegistry:
    return CredentialRegistry(local.credentials, local.environ(), home=me)


async def _check_evaluator_names(
    evaluators: Sequence[PythonEvaluator], api: AsyncApi, workspace: str
) -> None:
    remote = await HttpEvaluators(api).in_workspace(workspace)
    clashes = sorted({e.name for e in evaluators} & set(remote))
    if clashes:
        raise ValueError(
            f"python evaluator(s) {', '.join(clashes)} are also remote evaluators in @{workspace}"
        )


def _coerce(result: Any) -> RunResult:
    if isinstance(result, RunResult):
        return result
    if isinstance(result, str):
        return RunResult(answer=result)
    if isinstance(result, dict):
        return RunResult(output=result)
    raise TypeError(f"task returned {type(result).__name__}; return str, dict or RunResult")


def _root_attributes(
    run_id: str, exp_id: int | None, case: CaseView, repetition: int
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "session.id": run_id,
        "evalkit.case_id": case.case_id,
        "evalkit.repetition": repetition,
    }
    if exp_id is not None:
        attributes["evalkit.experiment_id"] = exp_id
    return attributes


async def _fixtures(blob: HttpBlobStore, case: CaseView) -> list[Fixture]:
    return [
        Fixture(name=file.key, path=await blob.fixture_path(file), context=file.context)
        for file in case.files
    ]


async def _flush(provider: Any) -> None:
    flush = getattr(provider, "force_flush", None)
    if flush is not None:
        await asyncio.to_thread(flush)


async def call_task_traced(
    task: Task,
    spec: TaskSpec,
    *,
    blob: HttpBlobStore,
    case: CaseView,
    repetition: int,
    exp_id: int | None,
    model: str | None,
    provider: Any,
) -> Outcome:
    run_id = str(uuid.uuid4())
    clock = time.monotonic()
    metadata = {
        "session_id": run_id,
        "experiment_id": exp_id,
        "case_id": case.case_id,
        "repetition": repetition,
        "tags": case.split_tags,
    }
    files = await _fixtures(blob, case)
    args, kwargs = arguments(spec, input=case.input, files=files, model=model, metadata=metadata)
    attributes = _root_attributes(run_id, exp_id, case, repetition)
    tracer = provider.get_tracer("evalkit.sdk")
    outcome = Outcome(run_id=run_id, started=datetime.now(UTC))
    token = CURRENT_RUN.set(attributes)
    recording = False
    try:
        with tracer.start_as_current_span(ROOT_SPAN_NAME, context=Context()) as span:
            recording = span.is_recording()
            if recording:
                span.set_attributes(
                    {
                        **attributes,
                        SDK_ROOT_ATTRIBUTE: True,
                        "openinference.span.kind": "CHAIN",
                        "input.value": json.dumps(case.input),
                    }
                )
            try:
                result = _coerce(await call_task(task, args, kwargs))
            except Exception as exc:
                if isinstance(exc, TypeError) and str(exc).startswith("task returned"):
                    outcome.error = f"task protocol violation: {exc}"
                else:
                    text = str(exc)
                    outcome.error = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
            else:
                _apply(outcome, result)
                if recording and outcome.session_id is not None:
                    span.set_attributes(
                        {"session.id": str(outcome.session_id), TASK_SESSION_ATTRIBUTE: True}
                    )
                if recording and (outcome.answer is not None or outcome.output is not None):
                    span.set_attribute(
                        "output.value",
                        outcome.answer
                        if outcome.answer is not None
                        else json.dumps(outcome.output),
                    )
    finally:
        CURRENT_RUN.reset(token)
    outcome.wall_ms = int((time.monotonic() - clock) * 1000)
    await _flush(provider)
    if outcome.session_id is None and recording:
        outcome.session_id = run_id
    if outcome.session_id is None and outcome.trajectory is None and outcome.error is None:
        outcome.error = NO_PROVIDER_ERROR
    return outcome


def _apply(outcome: Outcome, result: RunResult) -> None:
    if result.answer is None and result.output is None and result.error is None:
        outcome.error = (
            "task protocol violation: RunResult carries neither answer, output nor error"
        )
        return
    outcome.answer = result.answer
    outcome.output = result.output
    outcome.model = result.model
    outcome.error = result.error
    has_result = result.answer is not None or result.output is not None
    outcome.status = "completed" if has_result else "failed"
    outcome.session_id = result.session_id
    outcome.trajectory = result.trajectory


async def settle_and_record(
    deps: RunnerDeps,
    exp: Experiment,
    case: CaseView,
    repetition: int,
    outcome: Outcome,
    *,
    wait_seconds: int,
    model: str | None,
    poll_interval: float,
    holder: str | None = None,
    used_sessions: set[str] | None = None,
) -> RunSummary:
    if used_sessions is not None and outcome.session_id is not None:
        if outcome.session_id in used_sessions:
            outcome.error = (
                f"session id {outcome.session_id} was already used in experiment {exp.id}; "
                "each run needs its own session id"
            )
            outcome.status = "failed"
            outcome.session_id = None
            outcome.trajectory = None
        else:
            used_sessions.add(outcome.session_id)
    if outcome.session_id is not None:
        used = await deps.store.harness_run_id_used(exp.id, outcome.session_id)
        if used is not None:
            outcome.error = used
            outcome.status = "failed"
            outcome.session_id = None
            outcome.trajectory = None
    trajectory_doc: dict[str, Any] | None = None
    status: str | None
    if outcome.trajectory is not None:
        try:
            validate_trajectory(outcome.trajectory)
            trajectory_doc, status = outcome.trajectory, "valid"
        except TrajectoryValidationError as exc:
            status = "invalid"
            deps.progress(f"run {case.case_id} #{repetition}: pushed trajectory invalid: {exc}")
    elif outcome.session_id is None:
        status = "unavailable"
    else:
        assert deps.sessions is not None
        trajectory_doc, status = await poll_trajectory(
            deps.sessions,
            exp.agent,
            outcome.session_id,
            wait_seconds,
            poll_interval=poll_interval,
        )
    key: str | None = None
    metrics: dict[str, Any] = {}
    if trajectory_doc is not None:
        trajectory = validate_trajectory(trajectory_doc)
        backfill_step_costs(trajectory, load_cost_map(deps.cfg.cost_map), model or outcome.model)
        if status == "partial":
            trajectory.extra = {**(trajectory.extra or {}), "trajectory_status": status}
        key = trajectory_key(exp.agent_id, outcome.session_id or outcome.run_id)
        await deps.blob_store.put_object(
            key, json.dumps(trajectory.model_dump(exclude_none=True)).encode()
        )
        metrics = derive_metrics(TrajectoryView(trajectory), outcome.wall_ms).model_dump(
            exclude_none=True
        )
    metrics.setdefault("duration_ms", outcome.wall_ms)
    await deps.store.record_run(
        exp.id,
        case.case_id,
        repetition,
        AgentRunRecord(
            harness_run_id=outcome.session_id or outcome.run_id,
            status=RunStatus(outcome.status),
            answer=outcome.answer,
            output=outcome.output,
            error=outcome.error,
            started_at=outcome.started,
            ended_at=datetime.now(UTC),
            trajectory_key=key,
            trajectory_status=TrajectoryStatus(status) if status else None,
            metrics=metrics,
            title=case_title(case.input),
        ),
        holder=holder,
    )
    deps.progress(f"run {case.case_id} #{repetition} {outcome.status} (trajectory {status})")
    return RunSummary(
        case_id=case.case_id,
        repetition=repetition,
        status=outcome.status,
        trajectory_status=status,
        session_id=outcome.session_id,
    )


def _selection(case_ids: Sequence[str] | None, split: str | None) -> Selection:
    if case_ids:
        return Selection(case_ids=list(case_ids))
    if split:
        return Selection(tag=split)
    return Selection(all=True)


async def _username(api: AsyncApi, conn: connection.Connection, progress: Progress) -> str:
    me = (await api.get("/api/auth/me")).json()
    warning = connection.token_expiry_warning(conn, me.get("token_expires_at"), datetime.now(UTC))
    if warning is not None:
        progress(f"warning: {warning}")
    return str(me["username"])


def _note_how_to_finish(
    exc: Exception, exp_id: int, python_evaluators: Sequence[str], progress: Progress
) -> None:
    call = f"evalkit.sdk.evaluate_experiment(experiment_id={exp_id}"
    if python_evaluators:
        how = f"{call}, evaluators=[...]) passing {', '.join(python_evaluators)}"
    else:
        how = f"{call}) or `evalkit evaluate {exp_id}`"
    note = f"experiment {exp_id} is not fully scored; finish it with {how}"
    exc.add_note(note)
    progress(note)


def _experiment_url(server_url: str, exp_id: int) -> str:
    return f"{server_url.rstrip('/')}/experiments/{exp_id}"


async def _session_summary(api: AsyncApi, path: str, session_id: str) -> str:
    try:
        session = (await api.get(path, params={"esf": "true"})).json()
    except ApiRequestError as exc:
        if exc.status != 413:
            raise
        return f"  session {session_id}: {exc.detail}"
    kinds: dict[str, int] = {}
    for esf_span in session.get("esf") or []:
        kinds[esf_span["kind"]] = kinds.get(esf_span["kind"], 0) + 1
    version = session.get("mapping_version")
    return (
        f"  session {session_id}: {session['span_count']} spans "
        f"({', '.join(f'{k} {n}' for k, n in sorted(kinds.items())) or 'none'}), "
        f"mapping {f'v{version}' if version else 'built-in'}, "
        f"unmapped {', '.join(session.get('unmapped_attributes') or []) or 'none'}"
    )


async def _report_session(
    api: AsyncApi,
    sessions: SessionReader,
    agent_slug: str,
    session_id: str,
    *,
    wait: int,
    progress: Progress,
    poll_interval: float,
) -> str:
    doc, status = await poll_trajectory(
        sessions, agent_slug, session_id, wait, poll_interval=poll_interval
    )
    session_path = f"/api/agents/{encode(agent_slug)}/sessions/{encode(session_id)}/trajectory"
    progress(await _session_summary(api, session_path, session_id))
    steps = len((doc or {}).get("steps") or [])
    progress(f"  trajectory: {status}, {steps} steps")
    return status


async def _dry_run(
    api: AsyncApi,
    local: LocalConfig,
    spec: TaskSpec,
    task: Task,
    *,
    dataset_ref: Reference,
    agent_ref: Reference,
    me: str,
    selection: Selection,
    count: int,
    model: str | None,
    wait: int,
    provider: Any,
    progress: Progress,
    poll_interval: float,
) -> ExperimentResult:
    store = HttpStore(api)
    cases = (await store.cases_for(slug(dataset_ref), selection, agent=slug(agent_ref)))[:count]
    blob = HttpBlobStore(api, None)
    deps = RunnerDeps(
        cfg=local,
        store=store,
        blob_store=blob,
        agents=HttpAgents(api),
        evaluators=HttpEvaluators(api),
        credentials=_credentials(local, me),
        progress=progress,
        sessions=HttpSessionReader(api),
    )
    summaries: list[RunSummary] = []
    for case in cases:
        outcome = await call_task_traced(
            task,
            spec,
            blob=blob,
            case=case,
            repetition=1,
            exp_id=None,
            model=model,
            provider=provider,
        )
        progress(f"dry run {case.case_id}: {outcome.status}")
        progress(f"  answer: {outcome.answer if outcome.answer is not None else outcome.output}")
        if outcome.error:
            progress(f"  error: {outcome.error}")
        status: str | None = None
        if outcome.trajectory is not None:
            status = "valid"
            progress(f"  pushed trajectory: {len(outcome.trajectory.get('steps') or [])} steps")
        elif outcome.session_id is not None:
            assert deps.sessions is not None
            try:
                status = await _report_session(
                    api,
                    deps.sessions,
                    slug(agent_ref),
                    outcome.session_id,
                    wait=wait,
                    progress=progress,
                    poll_interval=poll_interval,
                )
            except ApiRequestError as exc:
                status = "unavailable"
                progress(f"  session {outcome.session_id}: trajectory read failed: {exc}")
        else:
            status = "unavailable"
        summaries.append(
            RunSummary(
                case_id=case.case_id,
                repetition=1,
                status=outcome.status,
                trajectory_status=status,
                session_id=outcome.session_id,
            )
        )
    return ExperimentResult(experiment_id=None, url=None, runs=summaries)


async def arun_experiment(
    *,
    dataset: str,
    agent: str,
    task: Task,
    workspace: str | None = None,
    evaluators: Sequence[PythonEvaluator] = (),
    repetitions: int = 1,
    concurrency: int | None = None,
    case_ids: Sequence[str] | None = None,
    split: str | None = None,
    model: str | None = None,
    resume: int | None = None,
    dry_run: bool | int = False,
    trajectory_wait_seconds: int | None = None,
    judge: JudgeSettings | None = None,
    api_transport: httpx.AsyncBaseTransport | None = None,
    custom_eval_transport: httpx.AsyncBaseTransport | None = None,
    progress: Progress = print,
    poll_interval: float = 5.0,
    tracer_provider: Any = None,
) -> ExperimentResult:
    """The async form of `run_experiment`, for code already in an event loop (a notebook).

    Same arguments and result as `run_experiment`. The arguments after `judge` exist
    for tests.
    """
    local = _local_config().resolve_env(scoring=not dry_run)
    spec = inspect_task(task)
    if model is not None and ignores_model(spec):
        progress(
            f"warning: model={model} is recorded on the experiment, but the task has no `model` "
            "parameter, so the agent never sees it"
        )
    active = connection.active()
    api = AsyncApi(active.url, active.token, transport=api_transport)
    try:
        await api.require_same_version()
        me = await _username(api, active, progress)
        definition = await _agent_definition(api, agent, me)
        await _check_evaluator_names(evaluators, api, definition.workspace)
        dataset_ref = resolve_dataset(dataset, workspace, me)
        agent_ref = definition.reference
        wait = (
            trajectory_wait_seconds
            if trajectory_wait_seconds is not None
            else definition.settings.trajectory_wait_seconds
        )
        provider = tracer_provider if tracer_provider is not None else trace.get_tracer_provider()
        on_disk = local.cases / dataset_ref.name if local.cases is not None else None
        own_dataset = dataset_ref.workspace == me and resume is None
        if own_dataset and on_disk is None:
            progress(
                f"no cases: directory in {local.path}; running {slug(dataset_ref)} as the "
                "server holds it, without syncing"
            )
        if own_dataset and on_disk is not None and on_disk.is_dir():
            await asyncio.to_thread(
                sync_dataset,
                SyncApi(active.url, active.token),
                on_disk,
                name=dataset_ref.name,
                workspace=me,
                revision=git_revision(on_disk),
                fixtures_dir=local.fixtures,
            )
        python_evaluators = {e.name: e for e in evaluators}
        if dry_run:
            return await _dry_run(
                api,
                local,
                spec,
                task,
                dataset_ref=dataset_ref,
                agent_ref=agent_ref,
                me=me,
                selection=_selection(case_ids, split),
                count=int(dry_run),
                model=model,
                wait=wait,
                provider=provider,
                progress=progress,
                poll_interval=poll_interval,
            )
        store = HttpStore(api)
        holder = local_holder(me)
        credentials = _credentials(local, me)
        if resume is None:
            resolved_judge = judge or JudgeSettings.model_validate(
                (await api.get("/api/judge-default")).json()
            )
            selected = await store.cases_for(
                slug(dataset_ref),
                _selection(case_ids, split),
                agent=slug(agent_ref),
            )
            judge_key_for(credentials, resolved_judge, selected)
            revision = git_revision(on_disk) if on_disk is not None and on_disk.is_dir() else None
            exp, dropped = await store.start_experiment(
                ExperimentRequest(
                    dataset=slug(dataset_ref),
                    agent=slug(agent_ref),
                    selection=_selection(case_ids, split),
                    repetitions=repetitions,
                    model=model,
                    capabilities=sdk_capabilities(definition),
                    cases=revision,
                    evalkit_version=version.current(),
                    code_evaluators=sorted(python_evaluators) or None,
                    judge=resolved_judge,
                    holder=holder,
                    trajectory_wait_seconds=trajectory_wait_seconds,
                )
            )
            if dropped:
                progress(dropped_cases_text(dropped))
        else:
            exp = await store.transition(resume, ExperimentStatus.RUNNING, holder)
        progress(f"experiment {exp.id}")
        blob = HttpBlobStore(api, exp.id)
        deps = RunnerDeps(
            cfg=local,
            store=store,
            blob_store=blob,
            agents=HttpAgents(api),
            evaluators=HttpEvaluators(api),
            credentials=credentials,
            custom_eval_transport=custom_eval_transport,
            progress=progress,
            sessions=HttpSessionReader(api),
            python_evaluators=python_evaluators,
        )
        cases = {case.case_id: case for case in await store.pinned_cases(exp.id)}
        for line in await missing_local_keys(deps, definition, cases.values()):
            progress(line)
        judge_key(deps, exp.config.judge, cases.values())
        slots = asyncio.Semaphore(max(concurrency or definition.settings.max_concurrent_runs, 1))
        summaries: list[RunSummary] = []
        used_sessions: set[str] = set()

        async def one(case: CaseView, repetition: int) -> None:
            async with slots:
                outcome = await call_task_traced(
                    task,
                    spec,
                    blob=blob,
                    case=case,
                    repetition=repetition,
                    exp_id=exp.id,
                    model=model,
                    provider=provider,
                )
            summaries.append(
                await settle_and_record(
                    deps,
                    exp,
                    case,
                    repetition,
                    outcome,
                    wait_seconds=wait,
                    model=model,
                    poll_interval=poll_interval,
                    holder=holder,
                    used_sessions=used_sessions,
                )
            )

        async def work() -> None:
            await asyncio.gather(
                *(
                    one(cases[pending.case_id], pending.repetition)
                    for pending in await store.pending_runs(exp.id)
                )
            )
            await store.transition(exp.id, ExperimentStatus.EVALUATING, holder)
            await evaluate_experiment(deps, exp.id, holder)

        try:
            await run_leased(deps, exp.id, holder, work())
        except LeaseLost as exc:
            note = f"resume it with run_experiment(..., resume={exp.id})"
            exc.add_note(note)
            progress(f"{exc}; {note}")
            raise
        except (ScoringFailed, ApiRequestError, httpx.TransportError) as exc:
            _note_how_to_finish(exc, exp.id, sorted(python_evaluators), progress)
            raise
        report = (await api.get(f"/api/experiments/{exp.id}/scorecard")).json()
        for line in summary_lines(report):
            progress(line)
        return ExperimentResult(
            experiment_id=exp.id,
            url=_experiment_url(active.url, exp.id),
            runs=sorted(summaries, key=lambda s: (s.case_id, s.repetition)),
        )
    finally:
        await api.aclose()


async def aevaluate_experiment(
    *,
    experiment_id: int,
    evaluators: Sequence[PythonEvaluator] = (),
    refetch_trajectories: bool = False,
    api_transport: httpx.AsyncBaseTransport | None = None,
    custom_eval_transport: httpx.AsyncBaseTransport | None = None,
    progress: Progress = print,
) -> ExperimentResult:
    """The async form of `evaluate_experiment`, for code already in an event loop.

    Same arguments and result as `evaluate_experiment`. The arguments after
    `refetch_trajectories` exist for tests.
    """
    local = _local_config().resolve_env(scoring=True)
    active = connection.active()
    api = AsyncApi(active.url, active.token, transport=api_transport)
    try:
        await api.require_same_version()
        store = HttpStore(api)
        exp = await store.get_experiment(experiment_id)
        await _check_evaluator_names(evaluators, api, parse_slug(exp.agent).workspace)
        declared = set(exp.config.snapshot.code_evaluators)
        unknown = sorted({e.name for e in evaluators} - declared)
        if unknown:
            raise ValueError(
                f"python evaluators {', '.join(unknown)} were not part of experiment "
                f"{experiment_id} (declared: {sorted(declared) or 'none'})"
            )
        me = active.username or ""
        holder = local_holder(me)
        deps = RunnerDeps(
            cfg=local,
            store=store,
            blob_store=HttpBlobStore(api, experiment_id),
            agents=HttpAgents(api),
            evaluators=HttpEvaluators(api),
            credentials=_credentials(local, me),
            custom_eval_transport=custom_eval_transport,
            progress=progress,
            sessions=HttpSessionReader(api),
            python_evaluators={e.name: e for e in evaluators},
        )
        await store.transition(experiment_id, ExperimentStatus.EVALUATING, holder)
        try:
            await rescore_experiment(
                deps, experiment_id, holder, refetch_trajectories=refetch_trajectories
            )
        except (ScoringFailed, ApiRequestError, httpx.TransportError) as exc:
            _note_how_to_finish(exc, experiment_id, sorted(declared), progress)
            raise
        runs = [
            RunSummary(
                case_id=r.case_id,
                repetition=r.repetition,
                status=r.agent_run.status.value,
                trajectory_status=r.agent_run.trajectory_status.value
                if r.agent_run.trajectory_status
                else None,
                session_id=r.agent_run.harness_run_id,
            )
            for r in await store.run_details(experiment_id)
        ]
        return ExperimentResult(
            experiment_id=experiment_id,
            url=_experiment_url(active.url, experiment_id),
            runs=runs,
        )
    finally:
        await api.aclose()


def _loop_is_running() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def run_experiment(
    *,
    dataset: str,
    agent: str,
    task: Task,
    workspace: str | None = None,
    evaluators: Sequence[PythonEvaluator] = (),
    repetitions: int = 1,
    concurrency: int | None = None,
    case_ids: Sequence[str] | None = None,
    split: str | None = None,
    model: str | None = None,
    resume: int | None = None,
    dry_run: bool | int = False,
    trajectory_wait_seconds: int | None = None,
    judge: JudgeSettings | None = None,
) -> ExperimentResult:
    """Run an experiment: call `task` once per case and repetition, store the runs on
    the server and score them.

    Scoring uses the judge, the remote evaluators of the agent's workspace and the
    Python `evaluators` you pass. Reads `evalkit.yaml` (cases, fixtures, credentials)
    from the current directory or a parent, and the login from `evalkit login`.

    Args:
        dataset: The dataset to run. A bare name means your personal workspace; if it
            is also a directory under `cases` in `evalkit.yaml`, it is synced first.
            `name@workspace` runs a shared dataset, every review state included.
        agent: The code-driven agent on the server that the runs belong to, created once
            with `evalkit agents create`. A bare name means your personal workspace.
        task: Your function, called once per case and repetition, sync or async. Its
            parameters bind by name from `input` (the case input), `files`
            (`list[Fixture]`), `model` and `metadata` (`session_id`, `experiment_id`,
            `case_id`, `repetition`, `tags`). A task with one parameter of another name
            receives the input. Return a `str` (the answer), a `dict` (the structured
            output) or a `RunResult`. A task that raises records a failed run.
        workspace: The workspace of `dataset` when it is a bare name.
        evaluators: Python evaluators made with `evaluator`. A case selects one by name
            under `evaluators.custom`, like an HTTP evaluator.
        repetitions: How many times each case runs.
        concurrency: How many tasks run at once. Defaults to the agent's
            `max_concurrent_runs`.
        case_ids: Run only these cases.
        split: Run only the cases of this split (a case tag).
        model: Passed to the task's `model` parameter and recorded on the experiment.
        resume: An experiment id to resume: runs only the cases it has not finished.
        dry_run: `True` runs one case without creating an experiment and prints the
            answer and what the server made of the session; an int runs that many.
        trajectory_wait_seconds: How long to wait for a run's spans to settle into a
            trajectory. Defaults to the agent's setting.
        judge: The judge for LLM-graded evaluators. Defaults to the server's default
            judge. Its API key comes from `credentials.judges` in `evalkit.yaml`, keyed
            by `base_url`.

    Returns:
        The experiment id, its URL and one summary per run.

    Raises:
        RuntimeError: When called from a running event loop; await `arun_experiment`
            there instead.
    """
    if _loop_is_running():
        raise RuntimeError(
            "run_experiment cannot be called from a running event loop; "
            "await arun_experiment(...) instead"
        )
    return asyncio.run(
        arun_experiment(
            dataset=dataset,
            agent=agent,
            task=task,
            workspace=workspace,
            evaluators=evaluators,
            repetitions=repetitions,
            concurrency=concurrency,
            case_ids=case_ids,
            split=split,
            model=model,
            resume=resume,
            dry_run=dry_run,
            trajectory_wait_seconds=trajectory_wait_seconds,
            judge=judge,
        )
    )


def evaluate_experiment_sync(
    *,
    experiment_id: int,
    evaluators: Sequence[PythonEvaluator] = (),
    refetch_trajectories: bool = False,
) -> ExperimentResult:
    """Re-score an experiment that used Python evaluators.

    `evalkit evaluate` refuses such experiments, because the evaluators live in your
    script; call this from the script instead. Exported as `evaluate_experiment`.

    Args:
        experiment_id: The experiment to re-score.
        evaluators: The Python evaluators the experiment ran with. Each must have been
            part of the experiment.
        refetch_trajectories: Read the trajectories of runs whose trajectory was
            `partial` or `unavailable` again before scoring.

    Returns:
        The experiment id, its URL and one summary per run.

    Raises:
        RuntimeError: When called from a running event loop; await
            `aevaluate_experiment` there instead.
    """
    if _loop_is_running():
        raise RuntimeError(
            "evaluate_experiment cannot be called from a running event loop; "
            "await aevaluate_experiment(...) instead"
        )
    return asyncio.run(
        aevaluate_experiment(
            experiment_id=experiment_id,
            evaluators=evaluators,
            refetch_trajectories=refetch_trajectories,
        )
    )
