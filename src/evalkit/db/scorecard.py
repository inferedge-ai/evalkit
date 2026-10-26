from typing import NamedTuple

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import aggregate_order_by, array_agg
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.contracts.enums import EvaluationSource, EvaluatorKind, RunStatus
from evalkit.core.scorecard import BUCKET_EVALUATOR, COST_GAUGE, EXCLUDED_FROM_BUCKETS
from evalkit.db import models
from evalkit.db.experiments import get_experiment, list_experiments
from evalkit.store.base import AggregateRows, HistoryPoint, Progress, RunState

Case, Evaluation, ExperimentRun, AgentRun = (
    models.Case,
    models.Evaluation,
    models.ExperimentRun,
    models.AgentRun,
)


def _source(source: str, producer: str) -> str:
    return "builtin" if source == EvaluationSource.BUILTIN.value else f"custom:{producer}"


def _scores(exp_ids: list[int]) -> Select:
    return (
        select(
            ExperimentRun.experiment_id,
            Case.case_id,
            Evaluation.name,
            func.avg(Evaluation.value),
            array_agg(aggregate_order_by(Evaluation.explanation, ExperimentRun.repetition))[1],
            func.min(Evaluation.bucket),
        )
        .join(ExperimentRun, ExperimentRun.id == Evaluation.experiment_run_id)
        .join(Case, Case.id == ExperimentRun.case_ref)
        .where(
            ExperimentRun.experiment_id.in_(exp_ids),
            Evaluation.error.is_(None),
            Evaluation.kind == EvaluatorKind.SCORE.value,
            Evaluation.value.is_not(None),
        )
        .group_by(ExperimentRun.experiment_id, Case.case_id, Evaluation.name)
    )


async def aggregate(session: AsyncSession, exp_id: int) -> AggregateRows:
    experiment = await get_experiment(session, exp_id)
    scores = await session.execute(_scores([exp_id]))
    gauges = await session.execute(
        select(Case.case_id, Evaluation.name, func.avg(Evaluation.value))
        .join(ExperimentRun, ExperimentRun.id == Evaluation.experiment_run_id)
        .join(Case, Case.id == ExperimentRun.case_ref)
        .where(
            ExperimentRun.experiment_id == exp_id,
            Evaluation.error.is_(None),
            Evaluation.kind == EvaluatorKind.GAUGE.value,
            Evaluation.value.is_not(None),
        )
        .group_by(Case.case_id, Evaluation.name)
    )
    errors = await session.execute(
        select(Case.case_id, Evaluation.name, func.max(Evaluation.error))
        .join(ExperimentRun, ExperimentRun.id == Evaluation.experiment_run_id)
        .join(Case, Case.id == ExperimentRun.case_ref)
        .where(ExperimentRun.experiment_id == exp_id, Evaluation.error.is_not(None))
        .group_by(Case.case_id, Evaluation.name)
    )
    runs = (
        await session.execute(
            select(Case.case_id, ExperimentRun.repetition, AgentRun.error)
            .join(Case, Case.id == ExperimentRun.case_ref)
            .join(AgentRun, AgentRun.id == ExperimentRun.agent_run_id)
            .where(ExperimentRun.experiment_id == exp_id)
            .order_by(Case.case_id, ExperimentRun.repetition)
        )
    ).all()
    sources = await session.execute(
        select(Evaluation.name, Evaluation.source, Evaluation.producer)
        .join(ExperimentRun, ExperimentRun.id == Evaluation.experiment_run_id)
        .where(ExperimentRun.experiment_id == exp_id)
        .distinct()
    )
    repetitions: dict[str, int] = {}
    for case_id, _, _ in runs:
        repetitions[case_id] = repetitions.get(case_id, 0) + 1
    return AggregateRows(
        experiment=experiment,
        case_scores=[(c, n, float(m), e, b) for _, c, n, m, e, b in scores.all()],
        case_gauges=[(c, n, float(m)) for c, n, m in gauges.all()],
        case_errors=[(c, n, e) for c, n, e in errors.all()],
        run_errors=[(c, e) for c, _, e in runs if e],
        repetitions_seen=repetitions,
        sources={name: _source(source, producer) for name, source, producer in sources.all()},
    )


FINISHED = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.TIMEOUT, RunStatus.CANCELLED}
)


class RunFacts(NamedTuple):
    finished: bool
    scorable: bool
    evaluated: bool


def case_state(repetitions: int, runs: list[RunFacts]) -> RunState:
    finished = [run for run in runs if run.finished]
    if not finished:
        return "pending"
    if len(finished) < repetitions:
        return "ran"
    if all(run.evaluated for run in finished if run.scorable):
        return "evaluated"
    return "ran"


async def case_states_many(
    session: AsyncSession, exp_ids: list[int]
) -> dict[int, dict[str, RunState]]:
    if not exp_ids:
        return {}
    repetitions: dict[int, int] = {
        exp_id: reps
        for exp_id, reps in (
            await session.execute(
                select(models.Experiment.id, models.Experiment.repetitions).where(
                    models.Experiment.id.in_(exp_ids)
                )
            )
        ).all()
    }
    pinned = await session.execute(
        select(models.ExperimentCase.experiment_id, Case.case_id)
        .join(Case, Case.id == models.ExperimentCase.case_ref)
        .where(models.ExperimentCase.experiment_id.in_(exp_ids))
    )
    has_evaluation = (
        select(Evaluation.id).where(Evaluation.experiment_run_id == ExperimentRun.id).exists()
    )
    runs = await session.execute(
        select(
            ExperimentRun.experiment_id,
            Case.case_id,
            AgentRun.status,
            AgentRun.error.is_(None) | AgentRun.output.is_not(None),
            has_evaluation,
        )
        .join(Case, Case.id == ExperimentRun.case_ref)
        .join(AgentRun, AgentRun.id == ExperimentRun.agent_run_id)
        .where(ExperimentRun.experiment_id.in_(exp_ids))
    )
    facts: dict[tuple[int, str], list[RunFacts]] = {}
    for exp_id, case_id, status, scorable, evaluated in runs.all():
        facts.setdefault((exp_id, case_id), []).append(
            RunFacts(RunStatus(status) in FINISHED, bool(scorable), bool(evaluated))
        )
    out: dict[int, dict[str, RunState]] = {exp_id: {} for exp_id in exp_ids}
    for exp_id, case_id in pinned.all():
        out[exp_id][case_id] = case_state(repetitions[exp_id], facts.get((exp_id, case_id), []))
    return out


async def case_states(session: AsyncSession, exp_id: int) -> dict[str, RunState]:
    await get_experiment(session, exp_id)
    return (await case_states_many(session, [exp_id]))[exp_id]


def _progress(states: dict[str, RunState]) -> Progress:
    return Progress(
        total=len(states),
        ran=sum(1 for s in states.values() if s != "pending"),
        evaluated=sum(1 for s in states.values() if s == "evaluated"),
    )


async def progress(session: AsyncSession, exp_id: int) -> Progress:
    return _progress(await case_states(session, exp_id))


async def progress_many(session: AsyncSession, exp_ids: list[int]) -> dict[int, Progress]:
    return {
        exp_id: _progress(states)
        for exp_id, states in (await case_states_many(session, exp_ids)).items()
    }


async def list_scores(
    session: AsyncSession, exp_ids: list[int]
) -> dict[int, dict[str, float | None]]:
    if not exp_ids:
        return {}
    per_name: dict[tuple[int, str, str], list[float]] = {}
    for exp_id, _, name, mean, _, bucket in (await session.execute(_scores(exp_ids))).all():
        if bucket is not None and name not in EXCLUDED_FROM_BUCKETS:
            per_name.setdefault((exp_id, bucket, name), []).append(float(mean))
    by_bucket: dict[tuple[int, str], list[float]] = {}
    for (exp_id, bucket, _), means in per_name.items():
        by_bucket.setdefault((exp_id, bucket), []).append(sum(means) / len(means))
    out: dict[int, dict[str, float | None]] = {exp_id: {} for exp_id in exp_ids}
    for (exp_id, bucket), values in by_bucket.items():
        out[exp_id][bucket] = sum(values) / len(values)
    return out


async def case_history(
    session: AsyncSession,
    dataset_id: int,
    case_ids: list[str] | None,
    *,
    limit: int,
    offset: int = 0,
) -> dict[str, list[HistoryPoint]]:
    experiments = await list_experiments(
        session, dataset_id=dataset_id, ran_case_ids=case_ids, limit=limit, offset=offset
    )
    if not experiments:
        return {}
    wanted = {*BUCKET_EVALUATOR.values(), COST_GAUGE}
    query = (
        select(
            ExperimentRun.experiment_id, Case.case_id, Evaluation.name, func.avg(Evaluation.value)
        )
        .join(ExperimentRun, ExperimentRun.id == Evaluation.experiment_run_id)
        .join(Case, Case.id == ExperimentRun.case_ref)
        .where(
            ExperimentRun.experiment_id.in_([e.id for e in experiments]),
            Evaluation.name.in_(wanted),
            Evaluation.error.is_(None),
            Evaluation.value.is_not(None),
        )
        .group_by(ExperimentRun.experiment_id, Case.case_id, Evaluation.name)
    )
    ran = (
        select(ExperimentRun.experiment_id, Case.case_id)
        .join(Case, Case.id == ExperimentRun.case_ref)
        .where(ExperimentRun.experiment_id.in_([e.id for e in experiments]))
        .distinct()
    )
    if case_ids is not None:
        query = query.where(Case.case_id.in_(case_ids))
        ran = ran.where(Case.case_id.in_(case_ids))
    means: dict[tuple[int, str], dict[str, float]] = {}
    for exp_id, case_id, name, mean in (await session.execute(query)).all():
        means.setdefault((exp_id, case_id), {})[name] = float(mean)
    seen = set((await session.execute(ran)).all())
    out: dict[str, list[HistoryPoint]] = {}
    for exp in experiments:
        for exp_id, case_id in sorted(k for k in seen if k[0] == exp.id):
            values = means.get((exp_id, case_id), {})
            out.setdefault(case_id, []).append(
                HistoryPoint(
                    experiment_id=exp.id,
                    number=exp.number,
                    agent=exp.agent,
                    model=exp.config.model,
                    created_by=exp.created_by,
                    created_at=exp.started_at,
                    repetitions=exp.repetitions,
                    scores={bucket: values.get(name) for bucket, name in BUCKET_EVALUATOR.items()},
                    cost_usd=values.get(COST_GAUGE),
                )
            )
    return out


async def list_costs(
    session: AsyncSession, exp_ids: list[int]
) -> dict[int, dict[str, float | None]]:
    if not exp_ids:
        return {}
    costs = await session.execute(
        select(ExperimentRun.experiment_id, func.avg(Evaluation.value))
        .join(ExperimentRun, ExperimentRun.id == Evaluation.experiment_run_id)
        .where(
            ExperimentRun.experiment_id.in_(exp_ids),
            Evaluation.name == COST_GAUGE,
            Evaluation.error.is_(None),
        )
        .group_by(ExperimentRun.experiment_id)
    )
    latencies = await session.execute(
        select(
            ExperimentRun.experiment_id,
            func.avg(func.extract("epoch", AgentRun.ended_at - AgentRun.started_at) * 1000),
        )
        .join(AgentRun, AgentRun.id == ExperimentRun.agent_run_id)
        .where(ExperimentRun.experiment_id.in_(exp_ids), AgentRun.ended_at.is_not(None))
        .group_by(ExperimentRun.experiment_id)
    )
    out: dict[int, dict[str, float | None]] = {
        exp_id: {"cost_usd": None, "avg_latency_ms": None} for exp_id in exp_ids
    }
    for exp_id, cost in costs.all():
        out[exp_id]["cost_usd"] = float(cost) if cost is not None else None
    for exp_id, latency in latencies.all():
        out[exp_id]["avg_latency_ms"] = float(latency) if latency is not None else None
    return out
