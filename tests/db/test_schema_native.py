import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from evalkit.db.models import (
    Agent,
    AgentRun,
    Case,
    CaseVersion,
    Dataset,
    DatasetCase,
    Evaluation,
    Experiment,
    ExperimentCase,
    ExperimentRun,
    User,
    Workspace,
)


async def _base(s):
    user = User(username="akshat", password_hash="x")
    s.add(user)
    await s.flush()
    ws = Workspace(name="dev")
    case = Case(case_id="q-1")
    s.add_all([ws, case])
    await s.flush()
    dataset = Dataset(workspace_id=ws.id, name="finance", kind="suite")
    version = CaseVersion(case_ref=case.id, content_hash="h1", input={}, evaluators={}, tags=["x"])
    agent = Agent(workspace_id=ws.id, name="ouragent")
    s.add_all([dataset, version, agent])
    await s.flush()
    return user, ws, case, dataset, agent


async def test_dataset_case_needs_an_existing_version(session_factory):
    async with session_factory() as s:
        _, _, case, dataset, _ = await _base(s)
        s.add(DatasetCase(dataset_id=dataset.id, case_ref=case.id, content_hash="nope"))
        with pytest.raises(IntegrityError, match="fk"):
            await s.commit()


async def _run(s, exp, case, agent, harness_run_id):
    from datetime import UTC, datetime

    run = AgentRun(
        agent_id=agent.id,
        harness_run_id=harness_run_id,
        status="completed",
        started_at=datetime.now(UTC),
    )
    s.add(run)
    await s.flush()
    s.add(ExperimentRun(experiment_id=exp.id, case_ref=case.id, repetition=1, agent_run_id=run.id))


async def _experiment(s):
    user, ws, case, dataset, agent = await _base(s)
    exp = Experiment(
        workspace_id=ws.id,
        dataset_id=dataset.id,
        number=1,
        agent_id=agent.id,
        created_by=user.id,
        status="running",
        repetitions=1,
        config={},
    )
    s.add(exp)
    await s.flush()
    s.add(
        ExperimentCase(
            experiment_id=exp.id, case_ref=case.id, content_hash="h1", effective_evaluators={}
        )
    )
    await s.flush()
    return exp, case, agent


async def _experiment_run(s) -> int:
    exp, case, agent = await _experiment(s)
    await _run(s, exp, case, agent, "r1")
    await s.flush()
    return await s.scalar(select(ExperimentRun.id))


async def test_one_experiment_run_per_case_and_repetition(session_factory):
    async with session_factory() as s:
        exp, case, agent = await _experiment(s)
        await _run(s, exp, case, agent, "r1")
        await s.flush()
        await _run(s, exp, case, agent, "r2")
        with pytest.raises(IntegrityError, match="uq_experiment_runs_case_repetition"):
            await s.commit()


async def test_evaluation_kind_is_checked(session_factory):
    async with session_factory() as s:
        exp_run_id = await _experiment_run(s)
        s.add(
            Evaluation(
                experiment_run_id=exp_run_id, producer="p", name="n", kind="bogus", source="builtin"
            )
        )
        with pytest.raises(IntegrityError, match="ck_evaluations_kind"):
            await s.commit()


async def test_tags_are_queryable_with_the_gin_operator(session_factory):
    async with session_factory() as s:
        await _base(s)
        await s.commit()
        found = await s.scalars(
            select(CaseVersion.content_hash).where(CaseVersion.tags.contains(["x"]))
        )
        assert found.all() == ["h1"]
