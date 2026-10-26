from datetime import UTC, datetime, timedelta

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from evalkit.contracts.enums import ExperimentStatus, RunStatus
from evalkit.core.access import WorkspaceRef, experiment_workspace
from evalkit.core.runner import ACTIVE, ALLOWED, LEASE_SECONDS
from evalkit.core.workspace import parse_slug
from evalkit.db import models
from evalkit.db.cases import case_views, dataset_by_ref
from evalkit.db.workspaces import to_ref, workspace_row
from evalkit.store.base import (
    CaseView,
    Conflict,
    Experiment,
    ExperimentConfig,
    NewExperiment,
    UnknownExperiment,
)

LEASE = timedelta(seconds=LEASE_SECONDS)
HomeWorkspace = aliased(models.Workspace)
DatasetWorkspace = aliased(models.Workspace)
AgentWorkspace = aliased(models.Workspace)


def _query() -> Select:
    return (
        select(
            models.Experiment,
            HomeWorkspace.name,
            models.Dataset.name,
            DatasetWorkspace.name,
            models.Agent.name,
            AgentWorkspace.name,
            models.User.username,
        )
        .join(HomeWorkspace, HomeWorkspace.id == models.Experiment.workspace_id)
        .join(models.Dataset, models.Dataset.id == models.Experiment.dataset_id)
        .join(DatasetWorkspace, DatasetWorkspace.id == models.Dataset.workspace_id)
        .join(models.Agent, models.Agent.id == models.Experiment.agent_id)
        .join(AgentWorkspace, AgentWorkspace.id == models.Agent.workspace_id)
        .join(models.User, models.User.id == models.Experiment.created_by)
    )


def _to_experiment(row) -> Experiment:
    exp, home, dataset, dataset_ws, agent, agent_ws, created_by = row
    return Experiment(
        id=exp.id,
        number=exp.number,
        workspace=home,
        dataset=f"{dataset}@{dataset_ws}",
        agent=f"{agent}@{agent_ws}",
        agent_id=exp.agent_id,
        created_by=created_by,
        status=ExperimentStatus(exp.status),
        status_error=exp.status_error,
        lease_holder=exp.lease_holder,
        lease_expires_at=exp.lease_expires_at,
        repetitions=exp.repetitions,
        config=ExperimentConfig.model_validate(exp.config),
        started_at=exp.started_at,
        ended_at=exp.ended_at,
    )


async def get_experiment(session: AsyncSession, exp_id: int) -> Experiment:
    row = (await session.execute(_query().where(models.Experiment.id == exp_id))).one_or_none()
    if row is None:
        raise UnknownExperiment(exp_id)
    return _to_experiment(row)


async def list_experiments(
    session: AsyncSession,
    *,
    dataset_id: int | None = None,
    workspace_id: int | None = None,
    status: ExperimentStatus | None = None,
    agent_id: int | None = None,
    ran_case_ids: list[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Experiment]:
    query = _query()
    if ran_case_ids is not None:
        query = query.where(
            select(models.ExperimentRun.id)
            .join(models.Case, models.Case.id == models.ExperimentRun.case_ref)
            .where(
                models.ExperimentRun.experiment_id == models.Experiment.id,
                models.Case.case_id.in_(ran_case_ids),
            )
            .exists()
        )
    if dataset_id is not None:
        query = query.where(models.Experiment.dataset_id == dataset_id)
    if workspace_id is not None:
        query = query.where(models.Experiment.workspace_id == workspace_id)
    if status is not None:
        query = query.where(models.Experiment.status == status.value)
    if agent_id is not None:
        query = query.where(models.Experiment.agent_id == agent_id)
    rows = await session.execute(
        query.order_by(models.Experiment.id.desc()).limit(limit).offset(offset)
    )
    return [_to_experiment(row) for row in rows.all()]


async def _agent(session: AsyncSession, ref: str) -> tuple[models.Agent, WorkspaceRef]:
    parsed = parse_slug(ref)
    workspace = await workspace_row(session, parsed.workspace)
    agent = await session.scalar(
        select(models.Agent).where(
            models.Agent.workspace_id == workspace.id, models.Agent.name == parsed.name
        )
    )
    if agent is None:
        raise KeyError(f"unknown agent: {ref}")
    return agent, to_ref(workspace)


async def _personal(session: AsyncSession, username: str) -> tuple[int, WorkspaceRef]:
    user = await session.scalar(select(models.User).where(models.User.username == username))
    if user is None:
        raise KeyError(f"unknown user: {username}")
    workspace = await session.scalar(
        select(models.Workspace).where(models.Workspace.owner_user_id == user.id)
    )
    assert workspace is not None
    return user.id, to_ref(workspace)


async def create_experiment(session: AsyncSession, req: NewExperiment) -> Experiment:
    dataset = await dataset_by_ref(session, req.dataset)
    dataset_row = await session.scalar(
        select(models.Dataset)
        .where(models.Dataset.id == dataset.id)
        .with_for_update(key_share=True)
    )
    assert dataset_row is not None
    dataset_ws = to_ref(await workspace_row(session, dataset.workspace))
    agent, agent_ws = await _agent(session, req.agent)
    runner_id, runner_ws = await _personal(session, req.runner)
    home = experiment_workspace(runner_ws, dataset_ws, agent_ws)
    number = await session.scalar(
        select(func.coalesce(func.max(models.Experiment.number), 0) + 1).where(
            models.Experiment.dataset_id == dataset.id
        )
    )
    exp = models.Experiment(
        workspace_id=home.id,
        dataset_id=dataset.id,
        number=number,
        agent_id=agent.id,
        created_by=runner_id,
        status=ExperimentStatus.RUNNING.value,
        lease_holder=req.holder,
        lease_expires_at=func.now() + LEASE,
        repetitions=req.repetitions,
        config=req.config.model_dump(mode="json"),
    )
    session.add(exp)
    await session.flush()
    views = await case_views(
        session,
        dataset_row.workspace_id,
        [(pinned.case_ref, pinned.content_hash) for pinned in req.cases],
        agent=req.agent,
        dataset_id=dataset.id,
    )
    session.add_all(
        models.ExperimentCase(
            experiment_id=exp.id,
            case_ref=view.case_ref,
            content_hash=view.content_hash,
            effective_evaluators=view.effective_evaluators,
        )
        for view in views
    )
    await session.flush()
    return await get_experiment(session, exp.id)


async def _compare_and_set(
    session: AsyncSession,
    exp_id: int,
    to: ExperimentStatus,
    holder: str | None,
    error: str | None,
) -> bool:
    allowed_from = [f.value for f, t in ALLOWED if t == to and f is not None]
    active = [s.value for s in ACTIVE]
    live = to in ACTIVE
    result = await session.execute(
        update(models.Experiment)
        .where(
            models.Experiment.id == exp_id,
            models.Experiment.status.in_(allowed_from),
            models.Experiment.status.not_in(active)
            | (models.Experiment.lease_expires_at < func.now())
            | (models.Experiment.lease_holder == holder),
        )
        .values(
            status=to.value,
            status_error=error,
            lease_holder=holder if live else None,
            lease_expires_at=func.now() + LEASE if live else None,
            ended_at=None if live else func.now(),
            updated_at=func.now(),
        )
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1  # type: ignore[attr-defined]


async def _has_pending_runs(session: AsyncSession, exp_id: int) -> bool:
    exp = await session.get(models.Experiment, exp_id)
    if exp is None:
        raise UnknownExperiment(exp_id)
    completed = (
        select(func.count())
        .select_from(models.ExperimentRun)
        .join(models.AgentRun, models.AgentRun.id == models.ExperimentRun.agent_run_id)
        .where(
            models.ExperimentRun.experiment_id == exp_id,
            models.AgentRun.status == RunStatus.COMPLETED.value,
        )
        .scalar_subquery()
    )
    pinned = (
        select(func.count())
        .select_from(models.ExperimentCase)
        .where(models.ExperimentCase.experiment_id == exp_id)
        .scalar_subquery()
    )
    found = (await session.execute(select(pinned, completed))).one()
    return found[0] * exp.repetitions > found[1]


async def has_pending_runs(session: AsyncSession, exp_id: int) -> bool:
    return await _has_pending_runs(session, exp_id)


def resumable(exp: Experiment, pending_runs: bool) -> bool:
    if exp.status is ExperimentStatus.FAILED:
        return True
    return exp.status is ExperimentStatus.COMPLETED and pending_runs


async def transition(
    session: AsyncSession,
    exp_id: int,
    to: ExperimentStatus,
    holder: str | None,
    error: str | None = None,
) -> Experiment:
    current = await get_experiment(session, exp_id)
    if current.status is to and (
        current.lease_holder == holder if to in ACTIVE else current.status_error == error
    ):
        return current
    if (
        current.status is ExperimentStatus.COMPLETED
        and to is ExperimentStatus.RUNNING
        and not await _has_pending_runs(session, exp_id)
    ):
        raise Conflict(f"experiment {exp_id} has no runs left to resume")
    if not await _compare_and_set(session, exp_id, to, holder, error):
        current = await get_experiment(session, exp_id)
        raise Conflict(
            f"experiment is {current.status.value}, held by {current.lease_holder or 'nobody'}"
        )
    session.expire_all()
    return await get_experiment(session, exp_id)


async def transition_stale(session: AsyncSession, exp_id: int) -> bool:
    holder = await session.scalar(
        select(models.Experiment.lease_holder).where(models.Experiment.id == exp_id)
    )
    return await _compare_and_set(
        session,
        exp_id,
        ExperimentStatus.FAILED,
        None,
        f"runner {holder} stopped responding",
    )


async def renew_lease(session: AsyncSession, exp_id: int, holder: str) -> bool:
    result = await session.execute(
        update(models.Experiment)
        .where(models.Experiment.id == exp_id, models.Experiment.lease_holder == holder)
        .values(lease_expires_at=func.now() + LEASE)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1  # type: ignore[attr-defined]


async def stale_active(session: AsyncSession, now: datetime) -> list[int]:
    rows = await session.scalars(
        select(models.Experiment.id)
        .where(
            models.Experiment.status.in_([s.value for s in ACTIVE]),
            models.Experiment.lease_expires_at < now,
        )
        .order_by(models.Experiment.id)
    )
    return list(rows.all())


async def set_model(session: AsyncSession, exp_id: int, model: str) -> None:
    row = await session.get(models.Experiment, exp_id)
    if row is None:
        raise UnknownExperiment(exp_id)
    row.config = {**row.config, "model": model}


async def pinned_cases(session: AsyncSession, exp_id: int) -> list[CaseView]:
    exp = await session.get(models.Experiment, exp_id)
    if exp is None:
        raise UnknownExperiment(exp_id)
    pinned = (
        await session.scalars(
            select(models.ExperimentCase).where(models.ExperimentCase.experiment_id == exp_id)
        )
    ).all()
    dataset = await session.get(models.Dataset, exp.dataset_id)
    assert dataset is not None
    views = await case_views(
        session,
        dataset.workspace_id,
        [(row.case_ref, row.content_hash) for row in pinned],
        agent=None,
        dataset_id=dataset.id,
    )
    frozen = {row.case_ref: row.effective_evaluators for row in pinned}
    return [
        view.model_copy(update={"effective_evaluators": frozen[view.case_ref]}) for view in views
    ]


async def reap(session_factory: async_sessionmaker[AsyncSession]) -> list[int]:
    async with session_factory() as s:
        reaped = [
            exp_id
            for exp_id in await stale_active(s, datetime.now(UTC))
            if await transition_stale(s, exp_id)
        ]
        await s.commit()
    return reaped


async def experiment_of_run(session: AsyncSession, experiment_run_id: int) -> int | None:
    return await session.scalar(
        select(models.ExperimentRun.experiment_id).where(
            models.ExperimentRun.id == experiment_run_id
        )
    )
