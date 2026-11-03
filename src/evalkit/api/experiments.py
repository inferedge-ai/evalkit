import asyncio
import logging
import uuid
from collections.abc import Coroutine
from dataclasses import replace
from typing import Any

import httpx2
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response, StreamingResponse

from evalkit.api.access import (
    ActorDep,
    StoresDep,
    TenantStores,
    ensure_active,
    load_workspace_ref,
    require,
    workspace_ref,
)
from evalkit.api.deps import ConfigDep, HolderDep, SessionDep
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
from evalkit.db.agents import agent_definition
from evalkit.db.cases import dataset_by_ref
from evalkit.db.experiments import tenant_of_experiment
from evalkit.db.models import AgentRun as AgentRunRow
from evalkit.db.models import ExperimentRun as ExperimentRunRow
from evalkit.db.models import User
from evalkit.db.runs import publish_evaluations as db_publish_evaluations
from evalkit.db.runs import record_run as db_record_run
from evalkit.db.store import PgStore
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


class ScoringOptions(BaseModel):
    evaluation_concurrency: int | None = Field(default=None, ge=1)


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


async def interrupt_tasks(session_factory, registry: TaskRegistry) -> None:
    tracked = list(registry.values())
    for _, _, task in tracked:
        task.cancel()
    await asyncio.gather(*(task for _, _, task in tracked), return_exceptions=True)
    results = await asyncio.gather(
        *(
            _fail_interrupted(session_factory, exp_id, holder)
            for exp_id, holder, task in tracked
            if task.cancelled()
        ),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            LOGGER.warning("could not mark an interrupted experiment failed: %s", result)


async def _fail_interrupted(session_factory, exp_id: int, holder: str) -> None:
    store = PgStore(session_factory, await tenant_of_experiment(session_factory, exp_id))
    await store.transition(exp_id, ExperimentStatus.FAILED, holder, error=INTERRUPTED_ERROR)


def slug_or_400(value: str) -> Reference:
    try:
        return parse_slug(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _creator_id(stores: TenantStores, session: AsyncSession, exp: Experiment) -> int | None:
    return await session.scalar(
        select(User.id).where(User.tenant_id == stores.tenant_id, User.username == exp.created_by)
    )


async def may_change(
    stores: TenantStores, session: AsyncSession, actor: Actor, exp: Experiment
) -> bool:
    ws = await load_workspace_ref(session, stores.tenant_id, exp.workspace)
    return can_change_experiment(actor, ws, await _creator_id(stores, session, exp))


async def _changeable(
    stores: TenantStores, session: AsyncSession, actor: Actor, exp_id: int
) -> Experiment:
    exp = await db_experiments.get_experiment(session, exp_id)
    require(await may_change(stores, session, actor, exp), "you cannot change this experiment")
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


def _scoring(
    request: Request, deps: RunnerDeps, asked: int | None, saved: int | None = None
) -> RunnerDeps:
    limit = request.app.state.cfg.runner.evaluation_concurrency
    if asked is not None and asked > limit:
        raise HTTPException(
            status_code=400,
            detail=f"evaluation_concurrency {asked} is above this server's limit of {limit}",
        )
    return replace(deps, evaluation_concurrency=asked or min(saved or limit, limit))


async def _runner_written(
    session: AsyncSession, tenant_id: int, exp: Experiment
) -> AgentDefinition:
    agent = await agent_definition(session, tenant_id, explicit(parse_slug(exp.agent)))
    if server_may_drive(agent):
        raise HTTPException(
            status_code=409,
            detail=f"experiment {exp.id} is driven by the server; only its runner writes to it",
        )
    return agent


async def _last_git_revision(
    session: AsyncSession, tenant_id: int, dataset: str
) -> GitRevision | None:
    row = await dataset_by_ref(session, tenant_id, dataset)
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
    stores: StoresDep,
    cfg: ConfigDep,
    session: SessionDep,
    actor: ActorDep,
    pod: HolderDep,
) -> dict:
    agent = await stores.agents.get(explicit(slug_or_400(body.agent)))
    if agent.archived:
        raise HTTPException(status_code=409, detail=f"agent {agent.ref} is archived")
    agent_ws = await workspace_ref(stores, agent.workspace)
    require(
        can_use_agent(actor, agent_ws),
        f"only the owner of @{agent.workspace} can run {agent.ref}",
    )
    dataset_ws = await workspace_ref(
        stores, (await dataset_by_ref(session, stores.tenant_id, body.dataset)).workspace
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
    if not server_driven and body.evaluation_concurrency is not None:
        raise HTTPException(
            status_code=400,
            detail="evaluation_concurrency is for server runs; your CLI or SDK scores this "
            "experiment, so pass it there",
        )
    if not server_driven and (body.capabilities is None or body.holder is None):
        raise HTTPException(
            status_code=400,
            detail="capabilities and holder are required for agents the server does not drive",
        )
    providers = stores.providers
    judge = body.judge or await providers.default_judge()
    if judge is None:
        raise HTTPException(status_code=400, detail=f"{NO_DEFAULT_JUDGE}, or pass a judge")
    api_key: str | None = None
    warnings: list[str] = []
    run_deps = stores.deps
    if server_driven:
        await require_registered_judge(providers, judge.base_url)
        keys, warnings = await server_keys(stores, cfg, agent, judge.base_url)
        api_key = keys.key_for("agent", agent.ref)
        run_deps = _scoring(request, with_keys(stores.deps, keys), body.evaluation_concurrency)
    holder = task_holder(pod) if server_driven else str(body.holder)
    try:
        caps = body.capabilities or await harness_capabilities(
            agent, api_key, stores.deps.harness_transport
        )
        exp, dropped = await prepare_experiment(
            stores.store,
            body,
            agent=agent,
            caps=caps,
            remote_evaluators=await stores.evaluators.in_workspace(agent.workspace),
            judge=judge,
            runner=actor.username,
            holder=holder,
            revision=None
            if body.cases is not None
            else await _last_git_revision(session, stores.tenant_id, body.dataset),
        )
    except (InvalidCaseInputs, NothingRunnable) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except httpx2.HTTPError as exc:
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
    stores: StoresDep,
    session: SessionDep,
    workspace: str | None = None,
    status: ExperimentStatus | None = None,
    limit: int = Query(50, ge=1),
    offset: int = Query(0, ge=0),
    brief: bool = False,
) -> list[dict]:
    workspace_id = (await workspace_ref(stores, workspace)).id if workspace else None
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
async def compare(base: int, compare: int, stores: StoresDep) -> dict:
    base_rows = await stores.store.aggregate(base)
    compare_rows = await stores.store.aggregate(compare)
    return {
        "base": assemble_report(base_rows).model_dump(),
        "compare": assemble_report(compare_rows, baseline=base_rows).model_dump(),
    }


@router.get("/{exp_id}")
async def get_experiment(
    exp_id: int, stores: StoresDep, session: SessionDep, actor: ActorDep
) -> dict:
    exp = await db_experiments.get_experiment(session, exp_id)
    pending_runs = await db_experiments.has_pending_runs(session, exp_id)
    return {
        **exp.model_dump(mode="json"),
        "progress": (await db_scorecard.progress(session, exp_id)).model_dump(),
        "case_states": await db_scorecard.case_states(session, exp_id),
        "can_change": await may_change(stores, session, actor, exp),
        "server_may_drive": server_may_drive(await _agent(stores.deps, exp)),
        "pending_runs": pending_runs,
        "resumable": db_experiments.resumable(exp, pending_runs),
    }


@router.get("/{exp_id}/cases")
async def pinned_cases(exp_id: int, stores: StoresDep) -> list[dict]:
    return [case.model_dump(mode="json") for case in await stores.store.pinned_cases(exp_id)]


@router.get("/{exp_id}/runs")
async def list_runs(exp_id: int, stores: StoresDep) -> list[dict]:
    exp = await stores.store.get_experiment(exp_id)
    rows = []
    for run in await stores.store.run_details(exp_id):
        if run.agent_run.trajectory_status in REFETCHABLE:
            _, status = await stored_trajectory_doc(stores.blob, exp.agent_id, run.agent_run)
            run.agent_run.trajectory_status = status
        rows.append(run.model_dump(mode="json"))
    return rows


@router.post("/{exp_id}/runs")
async def record_run(
    exp_id: int, body: RunPost, stores: StoresDep, session: SessionDep, actor: ActorDep
) -> dict:
    exp = await _changeable(stores, session, actor, exp_id)
    await _runner_written(session, stores.tenant_id, exp)
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
async def pending_runs(exp_id: int, stores: StoresDep) -> list[dict]:
    return [rep.model_dump() for rep in await stores.store.pending_runs(exp_id)]


@router.get("/{exp_id}/harness-run-id")
async def harness_run_id_used(exp_id: int, value: str, stores: StoresDep) -> dict:
    return {"used": await stores.store.harness_run_id_used(exp_id, value)}


@router.get("/{exp_id}/runs-to-score")
async def runs_to_score(exp_id: int, stores: StoresDep, force: bool = False) -> list[dict]:
    return [
        run.model_dump(mode="json") for run in await stores.store.runs_to_score(exp_id, force=force)
    ]


@runs_router.post("/{run_id}/evaluations")
async def publish_evaluations(
    run_id: int,
    body: EvaluationsPost,
    stores: StoresDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    exp_id = await db_experiments.experiment_of_run(session, run_id)
    if exp_id is None:
        raise HTTPException(status_code=404, detail=f"unknown experiment run: {run_id}")
    await _runner_written(
        session, stores.tenant_id, await _changeable(stores, session, actor, exp_id)
    )
    await db_publish_evaluations(
        session, run_id, body.rows, force=body.force, holder=body.holder, experiment_id=exp_id
    )
    await session.commit()
    return {"ok": True}


@router.patch("/{exp_id}")
async def patch_experiment(
    exp_id: int,
    body: ExperimentPatch,
    stores: StoresDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    agent = await _runner_written(
        session, stores.tenant_id, await _changeable(stores, session, actor, exp_id)
    )
    if body.status in ACTIVE and agent.archived:
        raise HTTPException(status_code=409, detail=f"agent {agent.ref} is archived")
    if body.model is not None:
        await stores.store.set_model(exp_id, body.model)
    if body.status is not None:
        await stores.store.transition(exp_id, body.status, body.holder, error=body.error)
    return (await stores.store.get_experiment(exp_id)).model_dump(mode="json")


@router.post("/{exp_id}/lease")
async def renew_lease(
    exp_id: int, body: LeaseRequest, stores: StoresDep, session: SessionDep, actor: ActorDep
) -> dict:
    await _changeable(stores, session, actor, exp_id)
    return {"renewed": await stores.store.renew_lease(exp_id, body.holder)}


@router.get("/{exp_id}/scorecard")
async def scorecard(exp_id: int, stores: StoresDep, baseline_id: int | None = None) -> dict:
    baseline = await stores.store.aggregate(baseline_id) if baseline_id is not None else None
    return assemble_report(await stores.store.aggregate(exp_id), baseline=baseline).model_dump()


@router.post("/{exp_id}/evaluate")
async def evaluate(
    exp_id: int,
    request: Request,
    stores: StoresDep,
    cfg: ConfigDep,
    session: SessionDep,
    actor: ActorDep,
    pod: HolderDep,
    body: ScoringOptions | None = None,
) -> dict:
    exp = await _changeable(stores, session, actor, exp_id)
    agent = await _server_driven(stores.deps, exp, "evaluate")
    asked = body.evaluation_concurrency if body is not None else None
    await require_registered_judge(stores.providers, exp.config.judge.base_url)
    keys, warnings = await server_keys(stores, cfg, agent, exp.config.judge.base_url)
    holder = task_holder(pod)
    exp = await stores.store.transition(exp_id, ExperimentStatus.EVALUATING, holder)
    _track(
        request,
        exp_id,
        holder,
        f"evaluate:{exp_id}",
        rescore_experiment(
            _scoring(
                request,
                with_keys(stores.deps, keys),
                asked,
                exp.config.snapshot.evaluation_concurrency,
            ),
            exp_id,
            holder,
        ),
    )
    return {**exp.model_dump(mode="json"), "warnings": warnings}


@router.post("/{exp_id}/resume")
async def resume(
    exp_id: int,
    request: Request,
    stores: StoresDep,
    cfg: ConfigDep,
    session: SessionDep,
    actor: ActorDep,
    pod: HolderDep,
    body: ScoringOptions | None = None,
) -> dict:
    exp = await _changeable(stores, session, actor, exp_id)
    agent = await _server_driven(stores.deps, exp, "resume")
    asked = body.evaluation_concurrency if body is not None else None
    await require_registered_judge(stores.providers, exp.config.judge.base_url)
    keys, warnings = await server_keys(stores, cfg, agent, exp.config.judge.base_url)
    holder = task_holder(pod)
    exp = await stores.store.transition(exp_id, ExperimentStatus.RUNNING, holder)
    _track(
        request,
        exp_id,
        holder,
        f"run:{exp_id}",
        run_experiment(
            _scoring(
                request,
                with_keys(stores.deps, keys),
                asked,
                exp.config.snapshot.evaluation_concurrency,
            ),
            exp_id,
            holder,
        ),
    )
    return {**exp.model_dump(mode="json"), "warnings": warnings}


@router.get("/{exp_id}/runs/{run_id}/trajectory")
async def stored_trajectory(exp_id: int, run_id: int, stores: StoresDep) -> dict:
    exp = await stores.store.get_experiment(exp_id)
    run = next(
        (r for r in await stores.store.run_details(exp_id) if r.experiment_run_id == run_id),
        None,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    doc, _ = await stored_trajectory_doc(stores.blob, exp.agent_id, run.agent_run)
    if doc is None:
        raise HTTPException(status_code=404, detail="run has no stored trajectory")
    trajectory = validate_trajectory(doc)
    # No-op for runs stored after per-step pricing landed; prices older
    # trajectories so the cost view matches the scorecard gauge.
    backfill_step_costs(trajectory, load_cost_map(stores.deps.cfg.cost_map))
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
async def list_blob(exp_id: int, prefix: str, stores: StoresDep) -> dict:
    _check_blob_key(await stores.store.get_experiment(exp_id), prefix)
    return {"objects": await stores.blob.list_objects(prefix)}


@router.put("/{exp_id}/blob/{key:path}")
async def put_blob(
    exp_id: int,
    key: str,
    request: Request,
    stores: StoresDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    exp = await _changeable(stores, session, actor, exp_id)
    await _runner_written(session, stores.tenant_id, exp)
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
    await session.close()
    await stores.blob.put_stream(key, request.stream())
    return {"ok": True}


async def _existing_blob_key(exp_id: int, key: str, stores: TenantStores) -> None:
    _check_blob_key(await stores.store.get_experiment(exp_id), key)
    if not await stores.blob.object_exists(key):
        raise HTTPException(status_code=404, detail="no such object")


@router.head("/{exp_id}/blob/{key:path}")
async def head_blob(exp_id: int, key: str, stores: StoresDep) -> Response:
    await _existing_blob_key(exp_id, key, stores)
    return Response(status_code=200)


@router.get("/{exp_id}/blob/{key:path}")
async def get_blob(exp_id: int, key: str, stores: StoresDep) -> Response:
    await _existing_blob_key(exp_id, key, stores)
    blob = await stores.blob.get_stream(key)
    return StreamingResponse(
        blob.chunks,
        media_type="application/octet-stream",
        headers={"Content-Length": str(blob.size)},
    )
