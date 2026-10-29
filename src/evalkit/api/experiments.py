import asyncio
import logging
import uuid
from collections.abc import Coroutine
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response, StreamingResponse

from evalkit.api.access import ActorDep, ensure_active, require, workspace_ref
from evalkit.api.deps import (
    AgentStoreDep,
    HolderDep,
    PgStoreDep,
    RunnerDepsDep,
    SessionDep,
)
from evalkit.api.llm_providers import NO_DEFAULT_JUDGE, require_registered_judge
from evalkit.api.server_keys import server_keys, with_keys
from evalkit.contracts.atif import validate_trajectory
from evalkit.contracts.enums import ExperimentStatus, SyncSource
from evalkit.core.access import (
    Actor,
    can_change_experiment,
    can_create_experiment,
    can_use_agent,
)
from evalkit.core.agents import AgentDefinition, explicit, server_may_drive
from evalkit.core.blob import trajectory_key
from evalkit.core.costmap import load_cost_map
from evalkit.core.runner import (
    ACTIVE,
    REFETCHABLE,
    ExperimentRequest,
    InvalidCaseInputs,
    NothingRunnable,
    RunnerDeps,
    error_text,
    harness_capabilities,
    prepare_experiment,
    rescore_experiment,
    run_experiment,
    stored_trajectory_doc,
)
from evalkit.core.scorecard import assemble_report
from evalkit.core.trajectory import backfill_step_costs
from evalkit.core.workspace import Reference, parse_slug
from evalkit.db import experiments as db_experiments
from evalkit.db import scorecard as db_scorecard
from evalkit.db.cases import dataset_by_ref
from evalkit.db.models import AgentRun as AgentRunRow
from evalkit.db.models import ExperimentRun as ExperimentRunRow
from evalkit.db.models import User
from evalkit.db.runs import record_run as db_record_run
from evalkit.store.base import (
    AgentRunRecord,
    EvaluationRow,
    Experiment,
    GitRevision,
    InvalidRun,
)

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/experiments", tags=["experiments"])
runs_router = APIRouter(prefix="/api/experiment-runs", tags=["experiments"])

INTERRUPTED_ERROR = "interrupted: evalkit server shut down"

TaskRegistry = dict[str, tuple[int, str, asyncio.Task]]
LIST_LIMIT = 200


class ExperimentPatch(BaseModel):
    status: ExperimentStatus | None = None
    error: str | None = None
    holder: str | None = None
    model: str | None = None


class LeaseRequest(BaseModel):
    holder: str


class RunPost(BaseModel):
    case_id: str
    repetition: int
    run: AgentRunRecord
    holder: str


class EvaluationsPost(BaseModel):
    rows: list[EvaluationRow]
    force: bool = False
    holder: str


def _consume_exception(task: asyncio.Task) -> None:
    def _cb(t: asyncio.Task) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            LOGGER.error(
                "experiment task %s failed: %s", t.get_name(), error_text(exc), exc_info=exc
            )

    task.add_done_callback(_cb)


def task_holder(pod: str) -> str:
    return f"{pod}:{uuid.uuid4().hex[:12]}"


def _track(
    request: Request, exp_id: int, holder: str, name: str, coro: Coroutine[Any, Any, None]
) -> None:
    task = asyncio.create_task(coro, name=name)
    _consume_exception(task)
    registry: TaskRegistry = request.app.state.tasks
    registry[name] = (exp_id, holder, task)
    task.add_done_callback(lambda t: registry.pop(name, None))


async def interrupt_tasks(store, registry: TaskRegistry) -> None:
    tracked = list(registry.values())
    for _, _, task in tracked:
        task.cancel()
    await asyncio.gather(*(task for _, _, task in tracked), return_exceptions=True)
    results = await asyncio.gather(
        *(
            store.transition(exp_id, ExperimentStatus.FAILED, holder, error=INTERRUPTED_ERROR)
            for exp_id, holder, task in tracked
            if task.cancelled()
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            LOGGER.warning("could not mark an interrupted experiment failed: %s", result)


def slug_or_400(value: str) -> Reference:
    try:
        return parse_slug(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _creator_id(session: AsyncSession, exp: Experiment) -> int | None:
    return await session.scalar(select(User.id).where(User.username == exp.created_by))


async def may_change(session: AsyncSession, actor: Actor, exp: Experiment) -> bool:
    ws = await workspace_ref(session, exp.workspace)
    return can_change_experiment(actor, ws, await _creator_id(session, exp))


async def _changeable(session: AsyncSession, actor: Actor, exp_id: int) -> Experiment:
    exp = await db_experiments.get_experiment(session, exp_id)
    require(await may_change(session, actor, exp), "you cannot change this experiment")
    return exp


async def _agent(deps: RunnerDeps, exp: Experiment) -> AgentDefinition:
    return await deps.agents.get(explicit(parse_slug(exp.agent)))


def _local_hint(agent: AgentDefinition, exp: Experiment, command: str) -> str:
    if command == "evaluate":
        return f"evalkit evaluate {exp.id}"
    if agent.harness_url is None:
        return f"run_experiment(..., resume={exp.id}) from your script"
    return f"evalkit run {exp.dataset} --agent {exp.agent} --resume {exp.id}"


async def _server_driven(deps: RunnerDeps, exp: Experiment, command: str) -> AgentDefinition:
    agent = await _agent(deps, exp)
    if not server_may_drive(agent):
        raise HTTPException(
            status_code=409, detail=f"{command} it with {_local_hint(agent, exp, command)}"
        )
    if agent.archived:
        raise HTTPException(status_code=409, detail=f"agent {agent.ref} is archived")
    return agent


async def _runner_written(deps: RunnerDeps, exp: Experiment) -> AgentDefinition:
    agent = await _agent(deps, exp)
    if server_may_drive(agent):
        raise HTTPException(
            status_code=409,
            detail=f"experiment {exp.id} is driven by the server; only its runner writes to it",
        )
    return agent


async def _last_git_revision(session: AsyncSession, dataset: str) -> GitRevision | None:
    row = await dataset_by_ref(session, dataset)
    sync = row.last_sync
    if sync is None or sync.source != SyncSource.GIT.value:
        return None
    return GitRevision(
        sha=sync.detail.get("sha"), branch=sync.detail.get("branch"), dirty=sync.detail.get("dirty")
    )


@router.post("")
async def create_experiment(
    body: ExperimentRequest,
    request: Request,
    deps: RunnerDepsDep,
    store: PgStoreDep,
    agents: AgentStoreDep,
    session: SessionDep,
    actor: ActorDep,
    pod: HolderDep,
) -> dict:
    agent = await agents.get(explicit(slug_or_400(body.agent)))
    if agent.archived:
        raise HTTPException(status_code=409, detail=f"agent {agent.ref} is archived")
    agent_ws = await workspace_ref(session, agent.workspace)
    require(
        can_use_agent(actor, agent_ws),
        f"only the owner of @{agent.workspace} can run {agent.ref}",
    )
    dataset_ws = await workspace_ref(
        session, (await dataset_by_ref(session, body.dataset)).workspace
    )
    ensure_active(agent_ws)
    ensure_active(dataset_ws)
    if dataset_ws.is_shared and agent_ws.is_shared:
        require(
            can_create_experiment(actor, dataset_ws),
            f"experiments of {body.dataset} with {agent.ref} belong to @{dataset_ws.name}; "
            "only its writers, reviewers and admins may start them",
        )
    server_driven = server_may_drive(agent)
    if server_driven and body.capabilities is not None:
        raise HTTPException(
            status_code=400, detail="capabilities are fetched by the server for shared agents"
        )
    if not server_driven and (body.capabilities is None or body.holder is None):
        raise HTTPException(
            status_code=400,
            detail="capabilities and holder are required for agents the server does not drive",
        )
    providers = request.app.state.llm_providers
    judge = body.judge or await providers.default_judge()
    if judge is None:
        raise HTTPException(status_code=400, detail=f"{NO_DEFAULT_JUDGE}, or pass a judge")
    api_key: str | None = None
    warnings: list[str] = []
    run_deps = deps
    if server_driven:
        await require_registered_judge(providers, judge.base_url)
        keys, warnings = await server_keys(request, agent, judge.base_url)
        api_key = keys.key_for("agent", agent.ref)
        run_deps = with_keys(deps, keys)
    holder = task_holder(pod) if server_driven else str(body.holder)
    try:
        caps = body.capabilities or await harness_capabilities(
            agent, api_key, deps.harness_transport
        )
        exp, dropped = await prepare_experiment(
            store,
            body,
            agent=agent,
            caps=caps,
            remote_evaluators=await deps.evaluators.in_workspace(agent.workspace),
            judge=judge,
            runner=actor.username,
            holder=holder,
            revision=None
            if body.cases is not None
            else await _last_git_revision(session, body.dataset),
        )
    except (InvalidCaseInputs, NothingRunnable) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"could not start experiment: {exc}") from exc
    if server_driven:
        _track(request, exp.id, holder, f"run:{exp.id}", run_experiment(run_deps, exp.id, holder))
    return {**exp.model_dump(mode="json"), "dropped_cases": dropped, "warnings": warnings}


async def with_progress(session: AsyncSession, experiments: list[Experiment]) -> list[dict]:
    ids = [e.id for e in experiments]
    scores = await db_scorecard.list_scores(session, ids)
    costs = await db_scorecard.list_costs(session, ids)
    progress = await db_scorecard.progress_many(session, ids)
    return [
        {
            **exp.model_dump(mode="json"),
            "progress": progress[exp.id].model_dump(),
            "scores": scores.get(exp.id, {}),
            **costs.get(exp.id, {}),
        }
        for exp in experiments
    ]


@router.get("")
async def list_experiments(
    session: SessionDep,
    workspace: str | None = None,
    status: ExperimentStatus | None = None,
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    brief: bool = False,
) -> list[dict]:
    workspace_id = (await workspace_ref(session, workspace)).id if workspace else None
    found = await db_experiments.list_experiments(
        session,
        workspace_id=workspace_id,
        status=status,
        limit=min(limit, LIST_LIMIT),
        offset=offset,
    )
    if brief:
        return [exp.model_dump(mode="json", exclude={"config"}) for exp in found]
    return await with_progress(session, found)


@router.get("/compare")
async def compare(base: int, compare: int, store: PgStoreDep) -> dict:
    base_rows = await store.aggregate(base)
    compare_rows = await store.aggregate(compare)
    return {
        "base": assemble_report(base_rows).model_dump(),
        "compare": assemble_report(compare_rows, baseline=base_rows).model_dump(),
    }


@router.get("/{exp_id}")
async def get_experiment(
    exp_id: int, deps: RunnerDepsDep, session: SessionDep, actor: ActorDep
) -> dict:
    exp = await db_experiments.get_experiment(session, exp_id)
    pending_runs = await db_experiments.has_pending_runs(session, exp_id)
    return {
        **exp.model_dump(mode="json"),
        "progress": (await db_scorecard.progress(session, exp_id)).model_dump(),
        "case_states": await db_scorecard.case_states(session, exp_id),
        "can_change": await may_change(session, actor, exp),
        "server_may_drive": server_may_drive(await _agent(deps, exp)),
        "pending_runs": pending_runs,
        "resumable": db_experiments.resumable(exp, pending_runs),
    }


@router.get("/{exp_id}/cases")
async def pinned_cases(exp_id: int, store: PgStoreDep) -> list[dict]:
    return [case.model_dump(mode="json") for case in await store.pinned_cases(exp_id)]


@router.get("/{exp_id}/runs")
async def list_runs(exp_id: int, deps: RunnerDepsDep, store: PgStoreDep) -> list[dict]:
    exp = await store.get_experiment(exp_id)
    rows = []
    for run in await store.run_details(exp_id):
        if run.agent_run.trajectory_status in REFETCHABLE:
            _, status = await stored_trajectory_doc(deps.blob_store, exp.agent_id, run.agent_run)
            run.agent_run.trajectory_status = status
        rows.append(run.model_dump(mode="json"))
    return rows


@router.post("/{exp_id}/runs")
async def record_run(
    exp_id: int, body: RunPost, deps: RunnerDepsDep, session: SessionDep, actor: ActorDep
) -> dict:
    exp = await _changeable(session, actor, exp_id)
    await _runner_written(deps, exp)
    expected = trajectory_key(exp.agent_id, body.run.harness_run_id)
    if body.run.trajectory_key is not None and body.run.trajectory_key != expected:
        raise HTTPException(status_code=400, detail=f"trajectory_key must be {expected}")
    try:
        ref = await db_record_run(
            session,
            exp_id,
            body.case_id,
            body.repetition,
            body.run,
            actor.id,
            holder=body.holder,
        )
    except InvalidRun as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return ref.model_dump()


@router.get("/{exp_id}/pending")
async def pending_runs(exp_id: int, store: PgStoreDep) -> list[dict]:
    return [rep.model_dump() for rep in await store.pending_runs(exp_id)]


@router.get("/{exp_id}/harness-run-id")
async def harness_run_id_used(exp_id: int, value: str, store: PgStoreDep) -> dict:
    return {"used": await store.harness_run_id_used(exp_id, value)}


@router.get("/{exp_id}/runs-to-score")
async def runs_to_score(exp_id: int, store: PgStoreDep, force: bool = False) -> list[dict]:
    return [run.model_dump(mode="json") for run in await store.runs_to_score(exp_id, force=force)]


@runs_router.post("/{run_id}/evaluations")
async def publish_evaluations(
    run_id: int,
    body: EvaluationsPost,
    deps: RunnerDepsDep,
    store: PgStoreDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    exp_id = await db_experiments.experiment_of_run(session, run_id)
    if exp_id is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
    await _runner_written(deps, await _changeable(session, actor, exp_id))
    await store.publish_evaluations(run_id, body.rows, force=body.force, holder=body.holder)
    return {"ok": True}


@router.patch("/{exp_id}")
async def patch_experiment(
    exp_id: int,
    body: ExperimentPatch,
    deps: RunnerDepsDep,
    store: PgStoreDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    agent = await _runner_written(deps, await _changeable(session, actor, exp_id))
    if body.status in ACTIVE and agent.archived:
        raise HTTPException(status_code=409, detail=f"agent {agent.ref} is archived")
    if body.model is not None:
        await store.set_model(exp_id, body.model)
    if body.status is not None:
        await store.transition(exp_id, body.status, body.holder, error=body.error)
    return (await store.get_experiment(exp_id)).model_dump(mode="json")


@router.post("/{exp_id}/lease")
async def renew_lease(
    exp_id: int, body: LeaseRequest, store: PgStoreDep, session: SessionDep, actor: ActorDep
) -> dict:
    await _changeable(session, actor, exp_id)
    return {"renewed": await store.renew_lease(exp_id, body.holder)}


@router.get("/{exp_id}/scorecard")
async def scorecard(exp_id: int, store: PgStoreDep, baseline_id: int | None = None) -> dict:
    baseline = await store.aggregate(baseline_id) if baseline_id is not None else None
    return assemble_report(await store.aggregate(exp_id), baseline=baseline).model_dump()


@router.post("/{exp_id}/evaluate")
async def evaluate(
    exp_id: int,
    request: Request,
    deps: RunnerDepsDep,
    store: PgStoreDep,
    session: SessionDep,
    actor: ActorDep,
    pod: HolderDep,
) -> dict:
    exp = await _changeable(session, actor, exp_id)
    agent = await _server_driven(deps, exp, "evaluate")
    await require_registered_judge(request.app.state.llm_providers, exp.config.judge.base_url)
    keys, warnings = await server_keys(request, agent, exp.config.judge.base_url)
    holder = task_holder(pod)
    exp = await store.transition(exp_id, ExperimentStatus.EVALUATING, holder)
    _track(
        request,
        exp_id,
        holder,
        f"evaluate:{exp_id}",
        rescore_experiment(with_keys(deps, keys), exp_id, holder),
    )
    return {**exp.model_dump(mode="json"), "warnings": warnings}


@router.post("/{exp_id}/resume")
async def resume(
    exp_id: int,
    request: Request,
    deps: RunnerDepsDep,
    store: PgStoreDep,
    session: SessionDep,
    actor: ActorDep,
    pod: HolderDep,
) -> dict:
    exp = await _changeable(session, actor, exp_id)
    agent = await _server_driven(deps, exp, "resume")
    await require_registered_judge(request.app.state.llm_providers, exp.config.judge.base_url)
    keys, warnings = await server_keys(request, agent, exp.config.judge.base_url)
    holder = task_holder(pod)
    exp = await store.transition(exp_id, ExperimentStatus.RUNNING, holder)
    _track(
        request,
        exp_id,
        holder,
        f"run:{exp_id}",
        run_experiment(with_keys(deps, keys), exp_id, holder),
    )
    return {**exp.model_dump(mode="json"), "warnings": warnings}


@router.get("/{exp_id}/runs/{run_id}/trajectory")
async def stored_trajectory(
    exp_id: int, run_id: int, deps: RunnerDepsDep, store: PgStoreDep
) -> dict:
    exp = await store.get_experiment(exp_id)
    run = next((r for r in await store.run_details(exp_id) if r.experiment_run_id == run_id), None)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    doc, _ = await stored_trajectory_doc(deps.blob_store, exp.agent_id, run.agent_run)
    if doc is None:
        raise HTTPException(status_code=404, detail="run has no stored trajectory")
    trajectory = validate_trajectory(doc)
    # No-op for runs stored after per-step pricing landed; prices older
    # trajectories so the cost view matches the scorecard gauge.
    backfill_step_costs(trajectory, load_cost_map(deps.cfg.cost_map))
    return trajectory.model_dump(exclude_none=True)


def _harness_run_id(exp: Experiment, key: str) -> str | None:
    for folder in ("trajectories", "workspaces"):
        prefix = f"{folder}/{exp.agent_id}/"
        if key.startswith(prefix) and key.endswith(".json"):
            return key.removeprefix(prefix).removesuffix(".json") or None
    run_id, _, path = key.removeprefix(f"artifacts/{exp.agent_id}/").partition("/")
    return run_id if run_id and path else None


def _check_blob_key(exp: Experiment, key: str) -> None:
    folders = ("trajectories", "artifacts", "workspaces")
    if not key.startswith(tuple(f"{folder}/{exp.agent_id}/" for folder in folders)):
        raise HTTPException(
            status_code=400,
            detail="key must be under this experiment's agent's "
            "trajectories/, artifacts/ or workspaces/",
        )


@router.get("/{exp_id}/blob")
async def list_blob(exp_id: int, prefix: str, deps: RunnerDepsDep, store: PgStoreDep) -> dict:
    _check_blob_key(await store.get_experiment(exp_id), prefix)
    return {"objects": await deps.blob_store.list_objects(prefix)}


@router.put("/{exp_id}/blob/{key:path}")
async def put_blob(
    exp_id: int,
    key: str,
    request: Request,
    deps: RunnerDepsDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    exp = await _changeable(session, actor, exp_id)
    await _runner_written(deps, exp)
    _check_blob_key(exp, key)
    harness_run_id = _harness_run_id(exp, key)
    if harness_run_id is None:
        raise HTTPException(status_code=400, detail=f"{key} names no run")
    recorded = (
        await session.execute(
            select(AgentRunRow.id, ExperimentRunRow.experiment_id, AgentRunRow.trajectory_key)
            .outerjoin(ExperimentRunRow, ExperimentRunRow.agent_run_id == AgentRunRow.id)
            .where(
                AgentRunRow.agent_id == exp.agent_id, AgentRunRow.harness_run_id == harness_run_id
            )
        )
    ).one_or_none()
    if recorded is not None and recorded[1] != exp_id:
        raise HTTPException(
            status_code=403, detail=f"run {harness_run_id} does not belong to experiment {exp_id}"
        )
    if recorded is not None and recorded[2] == key and exp.status is ExperimentStatus.RUNNING:
        raise HTTPException(
            status_code=409,
            detail=f"session id {harness_run_id} was already used in experiment {exp_id}; "
            "each run needs its own session id",
        )
    await deps.blob_store.put_stream(key, request.stream())
    return {"ok": True}


async def _existing_blob_key(exp_id: int, key: str, deps: RunnerDeps, store) -> None:
    _check_blob_key(await store.get_experiment(exp_id), key)
    if not await deps.blob_store.object_exists(key):
        raise HTTPException(status_code=404, detail="no such object")


@router.head("/{exp_id}/blob/{key:path}")
async def head_blob(exp_id: int, key: str, deps: RunnerDepsDep, store: PgStoreDep) -> Response:
    await _existing_blob_key(exp_id, key, deps, store)
    return Response(status_code=200)


@router.get("/{exp_id}/blob/{key:path}")
async def get_blob(exp_id: int, key: str, deps: RunnerDepsDep, store: PgStoreDep) -> Response:
    await _existing_blob_key(exp_id, key, deps, store)
    blob = await deps.blob_store.get_stream(key)
    return StreamingResponse(
        blob.chunks,
        media_type="application/octet-stream",
        headers={"Content-Length": str(blob.size)},
    )
