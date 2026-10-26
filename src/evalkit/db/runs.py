from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import Select, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.contracts.enums import EvaluationSource, EvaluatorKind, ExperimentStatus, RunStatus
from evalkit.core.runner import ACTIVE
from evalkit.core.scorecard import EXCLUDED_FROM_BUCKETS, bucket_for
from evalkit.db import models
from evalkit.db.cases import case_views
from evalkit.store.base import (
    AgentRunRecord,
    CaseRep,
    Conflict,
    EvaluationRow,
    InvalidRun,
    RunDetail,
    RunEvalContext,
    RunForScoring,
    RunRef,
    UnknownExperiment,
)

RunSource = Literal["app", "eval", "all"]


async def _experiment(session: AsyncSession, exp_id: int) -> models.Experiment:
    exp = await session.get(models.Experiment, exp_id)
    if exp is None:
        raise UnknownExperiment(exp_id)
    return exp


async def _leased_experiment(
    session: AsyncSession, exp_id: int, holder: str | None
) -> models.Experiment:
    exp = await session.scalar(
        select(models.Experiment).where(models.Experiment.id == exp_id).with_for_update(read=True)
    )
    if exp is None:
        raise UnknownExperiment(exp_id)
    if holder is not None and (
        exp.lease_holder != holder or ExperimentStatus(exp.status) not in ACTIVE
    ):
        raise Conflict(
            f"experiment {exp_id} is {exp.status}, held by {exp.lease_holder or 'nobody'}; "
            f"{holder} no longer holds its lease"
        )
    return exp


async def _used_harness_run_id(session: AsyncSession, agent_id: int, harness_run_id: str) -> str:
    row = (
        await session.execute(
            select(models.AgentRun.id, models.ExperimentRun.experiment_id)
            .outerjoin(
                models.ExperimentRun, models.ExperimentRun.agent_run_id == models.AgentRun.id
            )
            .where(
                models.AgentRun.agent_id == agent_id,
                models.AgentRun.harness_run_id == harness_run_id,
            )
        )
    ).one()
    where = f" in experiment {row[1]}" if row[1] is not None else ""
    return (
        f"run or session id {harness_run_id} was already used for agent run {row[0]}{where}; "
        "every run needs its own id"
    )


async def harness_run_id_used(
    session: AsyncSession, exp_id: int, harness_run_id: str
) -> str | None:
    exp = await _experiment(session, exp_id)
    taken = await session.scalar(
        select(models.AgentRun.id).where(
            models.AgentRun.agent_id == exp.agent_id,
            models.AgentRun.harness_run_id == harness_run_id,
        )
    )
    if taken is None:
        return None
    return await _used_harness_run_id(session, exp.agent_id, harness_run_id)


async def record_run(
    session: AsyncSession,
    exp_id: int,
    case_id: str,
    repetition: int,
    run: AgentRunRecord,
    created_by: int | None,
    *,
    holder: str | None = None,
) -> RunRef:
    exp = await _leased_experiment(session, exp_id, holder)
    if not 1 <= repetition <= exp.repetitions:
        raise InvalidRun(f"repetition {repetition} is outside 1..{exp.repetitions}")
    case_ref = await session.scalar(
        select(models.ExperimentCase.case_ref)
        .join(models.Case, models.Case.id == models.ExperimentCase.case_ref)
        .where(models.ExperimentCase.experiment_id == exp_id, models.Case.case_id == case_id)
    )
    if case_ref is None:
        raise InvalidRun(f"case {case_id} is not pinned in experiment {exp_id}")
    current = (
        await session.execute(
            select(models.ExperimentRun, models.AgentRun.status)
            .join(models.AgentRun, models.AgentRun.id == models.ExperimentRun.agent_run_id)
            .where(
                models.ExperimentRun.experiment_id == exp_id,
                models.ExperimentRun.case_ref == case_ref,
                models.ExperimentRun.repetition == repetition,
            )
            .with_for_update(of=models.ExperimentRun)
        )
    ).one_or_none()
    if current is not None and current[1] == RunStatus.COMPLETED.value:
        raise Conflict(f"{case_id} #{repetition} already has a completed run")
    agent_run_id = await session.scalar(
        insert(models.AgentRun)
        .values(
            agent_id=exp.agent_id,
            created_by=created_by if created_by is not None else exp.created_by,
            **run.model_dump(mode="json", exclude_none=True),
        )
        .on_conflict_do_nothing(constraint="uq_agent_runs_agent_harness_run")
        .returning(models.AgentRun.id)
    )
    if agent_run_id is None:
        raise Conflict(await _used_harness_run_id(session, exp.agent_id, run.harness_run_id))
    if current is None:
        experiment_run = models.ExperimentRun(
            experiment_id=exp_id,
            case_ref=case_ref,
            repetition=repetition,
            agent_run_id=agent_run_id,
        )
        session.add(experiment_run)
        await session.flush()
    else:
        experiment_run = current[0]
        experiment_run.agent_run_id = agent_run_id
        await session.execute(
            delete(models.Evaluation).where(
                models.Evaluation.experiment_run_id == experiment_run.id
            )
        )
        await session.flush()
    return RunRef(experiment_run_id=experiment_run.id, agent_run_id=agent_run_id)


async def pending_runs(session: AsyncSession, exp_id: int) -> list[CaseRep]:
    exp = await _experiment(session, exp_id)
    pinned = await session.execute(
        select(models.Case.case_id, models.ExperimentCase.case_ref)
        .join(models.Case, models.Case.id == models.ExperimentCase.case_ref)
        .where(models.ExperimentCase.experiment_id == exp_id)
    )
    completed = set(
        (
            await session.execute(
                select(models.ExperimentRun.case_ref, models.ExperimentRun.repetition)
                .join(models.AgentRun, models.AgentRun.id == models.ExperimentRun.agent_run_id)
                .where(
                    models.ExperimentRun.experiment_id == exp_id,
                    models.AgentRun.status == RunStatus.COMPLETED.value,
                )
            )
        ).all()
    )
    return [
        CaseRep(case_id=case_id, repetition=rep)
        for case_id, ref in sorted(pinned.all())
        for rep in range(1, exp.repetitions + 1)
        if (ref, rep) not in completed
    ]


def _record(row: models.AgentRun) -> AgentRunRecord:
    return AgentRunRecord.model_validate(row, from_attributes=True)


async def runs_to_score(session: AsyncSession, exp_id: int, *, force: bool) -> list[RunForScoring]:
    exp = await _experiment(session, exp_id)
    rows = (
        await session.execute(
            select(models.ExperimentRun, models.AgentRun, models.ExperimentCase)
            .join(models.AgentRun, models.AgentRun.id == models.ExperimentRun.agent_run_id)
            .join(
                models.ExperimentCase,
                (models.ExperimentCase.experiment_id == models.ExperimentRun.experiment_id)
                & (models.ExperimentCase.case_ref == models.ExperimentRun.case_ref),
            )
            .where(
                models.ExperimentRun.experiment_id == exp_id,
                models.AgentRun.error.is_(None) | models.AgentRun.output.is_not(None),
            )
            .order_by(models.ExperimentRun.id)
        )
    ).all()
    pinned = {ec.case_ref: ec for _, _, ec in rows}
    dataset = await session.get(models.Dataset, exp.dataset_id)
    assert dataset is not None
    views = {
        v.case_ref: v
        for v in await case_views(
            session,
            dataset.workspace_id,
            [(ref, ec.content_hash) for ref, ec in pinned.items()],
            agent=None,
            dataset_id=dataset.id,
        )
    }
    done: dict[int, set[str]] = {}
    if not force:
        succeeded = await session.execute(
            select(models.Evaluation.experiment_run_id, models.Evaluation.name).where(
                models.Evaluation.experiment_run_id.in_([er.id for er, _, _ in rows]),
                models.Evaluation.error.is_(None),
            )
        )
        for run_id, name in succeeded.all():
            done.setdefault(run_id, set()).add(name)
    return [
        RunForScoring(
            experiment_run_id=er.id,
            case=views[er.case_ref].model_copy(
                update={"effective_evaluators": pinned[er.case_ref].effective_evaluators}
            ),
            repetition=er.repetition,
            agent_run=_record(ar),
            agent_run_id=ar.id,
            done=done.get(er.id, set()),
        )
        for er, ar, _ in rows
    ]


def _bucket(row: EvaluationRow) -> str | None:
    if row.error is not None or row.kind is EvaluatorKind.GAUGE:
        return None
    if row.name in EXCLUDED_FROM_BUCKETS:
        return None
    source = "builtin" if row.source is EvaluationSource.BUILTIN else f"custom:{row.producer}"
    return bucket_for(row.name, source)


async def publish_evaluations(
    session: AsyncSession,
    experiment_run_id: int,
    rows: list[EvaluationRow],
    *,
    force: bool = False,
    holder: str | None = None,
) -> None:
    if holder is not None:
        exp_id = await session.scalar(
            select(models.ExperimentRun.experiment_id).where(
                models.ExperimentRun.id == experiment_run_id
            )
        )
        if exp_id is None:
            raise InvalidRun(f"unknown experiment run: {experiment_run_id}")
        await _leased_experiment(session, exp_id, holder)
    succeeded = {r.producer for r in rows if r.error is None}
    if succeeded:
        await session.execute(
            delete(models.Evaluation).where(
                models.Evaluation.experiment_run_id == experiment_run_id,
                models.Evaluation.producer.in_(succeeded),
                models.Evaluation.error.is_not(None),
            )
        )
    for row in rows:
        values = {
            **row.model_dump(mode="json"),
            "experiment_run_id": experiment_run_id,
            "bucket": _bucket(row),
        }
        statement = insert(models.Evaluation).values(**values)
        update_set = {k: statement.excluded[k] for k in values if k != "experiment_run_id"}
        upsert = statement.on_conflict_do_update(
            constraint="uq_evaluations_run_name",
            set_=update_set,
            where=None if force else models.Evaluation.error.is_not(None),
        )
        await session.execute(upsert)


def _evaluation(row: models.Evaluation) -> EvaluationRow:
    return EvaluationRow(
        producer=row.producer,
        name=row.name,
        kind=EvaluatorKind(row.kind),
        source=EvaluationSource(row.source),
        value=row.value,
        label=row.label,
        explanation=row.explanation,
        error=row.error,
    )


async def run_details(session: AsyncSession, exp_id: int) -> list[RunDetail]:
    await _experiment(session, exp_id)
    rows = (
        await session.execute(
            select(models.ExperimentRun, models.AgentRun, models.Case.case_id)
            .join(models.AgentRun, models.AgentRun.id == models.ExperimentRun.agent_run_id)
            .join(models.Case, models.Case.id == models.ExperimentRun.case_ref)
            .where(models.ExperimentRun.experiment_id == exp_id)
            .order_by(models.Case.case_id, models.ExperimentRun.repetition)
        )
    ).all()
    evaluations: dict[int, list[EvaluationRow]] = {}
    if rows:
        found = await session.scalars(
            select(models.Evaluation)
            .where(models.Evaluation.experiment_run_id.in_([er.id for er, _, _ in rows]))
            .order_by(models.Evaluation.id)
        )
        for row in found.all():
            evaluations.setdefault(row.experiment_run_id, []).append(_evaluation(row))
    return [
        RunDetail(
            experiment_run_id=er.id,
            case_id=case_id,
            repetition=er.repetition,
            agent_run_id=ar.id,
            agent_run=_record(ar),
            evaluations=evaluations.get(er.id, []),
        )
        for er, ar, case_id in rows
    ]


class AgentRunItem(BaseModel):
    id: int
    harness_run_id: str
    title: str | None
    status: RunStatus
    started_at: datetime
    created_by: str | None
    experiment_id: int | None
    experiment_number: int | None
    case_id: str | None
    trajectory_key: str | None


def _items_query() -> Select:
    return (
        select(
            models.AgentRun,
            models.User.username,
            models.Experiment.id,
            models.Experiment.number,
            models.Case.case_id,
        )
        .outerjoin(models.User, models.User.id == models.AgentRun.created_by)
        .outerjoin(models.ExperimentRun, models.ExperimentRun.agent_run_id == models.AgentRun.id)
        .outerjoin(models.Experiment, models.Experiment.id == models.ExperimentRun.experiment_id)
        .outerjoin(models.Case, models.Case.id == models.ExperimentRun.case_ref)
    )


def _item(row) -> AgentRunItem:
    run, username, exp_id, number, case_id = row
    return AgentRunItem(
        id=run.id,
        harness_run_id=run.harness_run_id,
        title=run.title,
        status=RunStatus(run.status),
        started_at=run.started_at,
        created_by=username,
        experiment_id=exp_id,
        experiment_number=number,
        case_id=case_id,
        trajectory_key=run.trajectory_key,
    )


async def list_agent_runs(
    session: AsyncSession,
    agent_id: int,
    *,
    query: str | None,
    limit: int,
    offset: int = 0,
    created_by: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    source: RunSource = "all",
) -> tuple[list[AgentRunItem], int]:
    stmt = _items_query().where(models.AgentRun.agent_id == agent_id)
    if since is not None:
        stmt = stmt.where(models.AgentRun.started_at >= since)
    if until is not None:
        stmt = stmt.where(models.AgentRun.started_at < until)
    if source == "eval":
        stmt = stmt.where(models.ExperimentRun.id.is_not(None))
    elif source == "app":
        stmt = stmt.where(models.ExperimentRun.id.is_(None))
    if created_by is not None:
        stmt = stmt.where(models.User.username == created_by)
    if query:
        stmt = stmt.where(
            models.AgentRun.title.ilike(f"%{query}%")
            | models.Case.case_id.ilike(f"%{query}%")
            | models.AgentRun.harness_run_id.startswith(query)
        )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = await session.execute(
        stmt.order_by(models.AgentRun.started_at.desc(), models.AgentRun.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [_item(row) for row in rows.all()], total or 0


def eval_run_ids(agent_id: int) -> Select:
    return (
        select(models.AgentRun.harness_run_id)
        .join(models.ExperimentRun, models.ExperimentRun.agent_run_id == models.AgentRun.id)
        .where(models.AgentRun.agent_id == agent_id)
    )


async def runs_by_harness_ids(
    session: AsyncSession, agent_id: int, harness_run_ids: list[str]
) -> dict[str, AgentRunItem]:
    if not harness_run_ids:
        return {}
    rows = await session.execute(
        _items_query().where(
            models.AgentRun.agent_id == agent_id,
            models.AgentRun.harness_run_id.in_(harness_run_ids),
        )
    )
    return {item.harness_run_id: item for item in map(_item, rows.all())}


async def trajectory_keys(
    session: AsyncSession, agent_id: int, harness_run_ids: list[str]
) -> dict[str, str]:
    if not harness_run_ids:
        return {}
    rows = await session.execute(
        select(models.AgentRun.harness_run_id, models.AgentRun.trajectory_key).where(
            models.AgentRun.agent_id == agent_id,
            models.AgentRun.harness_run_id.in_(harness_run_ids),
            models.AgentRun.trajectory_key.is_not(None),
        )
    )
    return {run_id: key for run_id, key in rows.all()}


async def agent_run(
    session: AsyncSession, agent_id: int, harness_run_id: str
) -> AgentRunRecord | None:
    row = await session.scalar(
        select(models.AgentRun).where(
            models.AgentRun.agent_id == agent_id,
            models.AgentRun.harness_run_id == harness_run_id,
        )
    )
    return None if row is None else _record(row)


async def eval_context(
    session: AsyncSession, agent_id: int, harness_run_id: str
) -> RunEvalContext | None:
    row = (
        await session.execute(
            select(models.ExperimentRun, models.ExperimentCase, models.Experiment)
            .join(models.AgentRun, models.AgentRun.id == models.ExperimentRun.agent_run_id)
            .join(models.Experiment, models.Experiment.id == models.ExperimentRun.experiment_id)
            .join(
                models.ExperimentCase,
                (models.ExperimentCase.experiment_id == models.ExperimentRun.experiment_id)
                & (models.ExperimentCase.case_ref == models.ExperimentRun.case_ref),
            )
            .where(
                models.AgentRun.agent_id == agent_id,
                models.AgentRun.harness_run_id == harness_run_id,
            )
        )
    ).one_or_none()
    if row is None:
        return None
    experiment_run, pinned, exp = row
    dataset = await session.get(models.Dataset, exp.dataset_id)
    assert dataset is not None
    (view,) = await case_views(
        session,
        dataset.workspace_id,
        [(pinned.case_ref, pinned.content_hash)],
        agent=None,
        dataset_id=dataset.id,
    )
    found = await session.scalars(
        select(models.Evaluation)
        .where(models.Evaluation.experiment_run_id == experiment_run.id)
        .order_by(models.Evaluation.id)
    )
    return RunEvalContext(
        experiment_id=exp.id,
        repetition=experiment_run.repetition,
        case=view.model_copy(update={"effective_evaluators": pinned.effective_evaluators}),
        evaluations=[_evaluation(e) for e in found.all()],
    )


async def experiment_run_ids(
    session: AsyncSession, exp_id: int, *, limit: int, offset: int
) -> tuple[int, list[str], int]:
    exp = await _experiment(session, exp_id)
    stmt = (
        select(models.AgentRun.harness_run_id)
        .join(models.ExperimentRun, models.ExperimentRun.agent_run_id == models.AgentRun.id)
        .join(models.Case, models.Case.id == models.ExperimentRun.case_ref)
        .where(models.ExperimentRun.experiment_id == exp_id)
    )
    total = await session.scalar(select(func.count()).select_from(stmt.subquery()))
    ids = await session.scalars(
        stmt.order_by(models.Case.case_id, models.ExperimentRun.repetition)
        .limit(limit)
        .offset(offset)
    )
    return exp.agent_id, list(ids.all()), total or 0
