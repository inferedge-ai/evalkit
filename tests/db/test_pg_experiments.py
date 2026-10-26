import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from evalkit.contracts.enums import ExperimentStatus, RunStatus, TrajectorySource
from evalkit.contracts.settings import JudgeSettings
from evalkit.db.cases import current_versions, dataset_by_ref
from evalkit.db.experiments import (
    create_experiment,
    get_experiment,
    list_experiments,
    renew_lease,
    stale_active,
    transition,
    transition_stale,
)
from evalkit.db.models import Case, ExperimentCase
from evalkit.db.models import Experiment as ExperimentRow
from evalkit.db.runs import record_run
from evalkit.store.base import (
    AgentRunRecord,
    Conflict,
    ExperimentConfig,
    ExperimentSnapshot,
    NewExperiment,
    PinnedVersion,
    Selection,
    UnknownExperiment,
)
from tests.identity import seed_agent
from tests.seed import World, incoming, overlay, seed_world, sync

RUNNING, EVALUATING, COMPLETED, FAILED = (
    ExperimentStatus.RUNNING,
    ExperimentStatus.EVALUATING,
    ExperimentStatus.COMPLETED,
    ExperimentStatus.FAILED,
)
CONFIG = ExperimentConfig(
    judge=JudgeSettings(base_url="http://judge", model="j"),
    selection=Selection(all=True),
    snapshot=ExperimentSnapshot(
        capabilities={}, trajectory_source=TrajectorySource.ATIF, trajectory_wait_seconds=60
    ),
)


@pytest.fixture
async def world(session_factory) -> World:
    world = await seed_world(session_factory)
    await seed_agent(session_factory, "ouragent", "dev")
    await seed_agent(session_factory, "support-bot", "dev")
    await seed_agent(session_factory, "myagent", "akshat")
    async with session_factory() as s:
        for workspace, actor in ((world.dev, world.admin_id), (world.akshat_ws, world.akshat_id)):
            await sync(s, workspace, actor, [incoming("q-1"), incoming("q-2")])
        await s.commit()
    return world


async def new(s, dataset="finance@dev", agent="ouragent@dev", cases=("q-1",), holder="pod-a"):
    row = await dataset_by_ref(s, dataset)
    current = dict(await current_versions(s, row.id))
    refs = dict((await s.execute(select(Case.case_id, Case.id))).all())
    return NewExperiment(
        dataset=dataset,
        agent=agent,
        runner="akshat",
        repetitions=2,
        config=CONFIG,
        cases=[
            PinnedVersion(case_ref=refs[case_id], content_hash=current[refs[case_id]])
            for case_id in cases
        ],
        holder=holder,
    )


@pytest.fixture
async def running_exp(session_factory, world):
    async with session_factory() as s:
        exp = await create_experiment(s, await new(s))
        await s.commit()
    return exp


async def _expire(session_factory, exp_id: int) -> None:
    async with session_factory() as s:
        await s.execute(
            update(ExperimentRow)
            .where(ExperimentRow.id == exp_id)
            .values(lease_expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await s.commit()


async def test_create_starts_running_with_a_lease(running_exp):
    assert running_exp.status is RUNNING
    assert running_exp.lease_holder == "pod-a"
    assert running_exp.lease_expires_at is not None
    assert (running_exp.dataset, running_exp.agent) == ("finance@dev", "ouragent@dev")
    assert running_exp.created_by == "akshat"
    assert running_exp.config == CONFIG


async def test_transition_is_compare_and_set(session_factory, running_exp):
    async with session_factory() as s:
        with pytest.raises(Conflict, match="experiment is running, held by pod-a"):
            await transition(s, running_exp.id, RUNNING, holder="pod-b")
        with pytest.raises(Conflict):
            await transition(s, running_exp.id, FAILED, holder="pod-b", error="x")
        exp = await transition(s, running_exp.id, FAILED, holder="pod-a", error="x")
        await s.commit()
    assert exp.status is FAILED and exp.lease_holder is None and exp.lease_expires_at is None
    assert exp.status_error == "x" and exp.ended_at is not None


async def test_completed_experiment_resumes_only_while_runs_are_missing(
    session_factory, running_exp
):
    attempt = AgentRunRecord(
        harness_run_id="h", status=RunStatus.COMPLETED, started_at=datetime.now(UTC)
    )
    async with session_factory() as s:
        await transition(s, running_exp.id, EVALUATING, holder="pod-a")
        await transition(s, running_exp.id, COMPLETED, holder="pod-a")
        resumed = await transition(s, running_exp.id, RUNNING, holder="pod-b")
        for rep in (1, 2):
            await record_run(
                s,
                running_exp.id,
                "q-1",
                rep,
                attempt.model_copy(update={"harness_run_id": f"h{rep}"}),
                None,
            )
        await transition(s, running_exp.id, EVALUATING, holder="pod-b")
        await transition(s, running_exp.id, COMPLETED, holder="pod-b")
        with pytest.raises(Conflict, match="no runs left to resume"):
            await transition(s, running_exp.id, RUNNING, holder="pod-c")
    assert resumed.status is RUNNING and resumed.lease_holder == "pod-b"


async def test_repeating_a_live_transition_by_its_holder_succeeds(session_factory, running_exp):
    async with session_factory() as s:
        first = await transition(s, running_exp.id, EVALUATING, holder="pod-a")
        again = await transition(s, running_exp.id, EVALUATING, holder="pod-a")
    assert first.status is again.status is EVALUATING


async def test_resume_clears_error_and_takes_lease(session_factory, running_exp):
    async with session_factory() as s:
        await transition(s, running_exp.id, FAILED, holder="pod-a", error="boom")
        exp = await transition(s, running_exp.id, RUNNING, holder="cli:akshat@laptop:1")
        await s.commit()
    assert exp.status is RUNNING and exp.status_error is None and exp.ended_at is None
    assert exp.lease_holder == "cli:akshat@laptop:1"


async def test_second_resume_while_live_conflicts(session_factory, running_exp):
    async with session_factory() as s:
        await transition(s, running_exp.id, FAILED, holder="pod-a", error="boom")
        await transition(s, running_exp.id, RUNNING, holder="pod-b")
        with pytest.raises(Conflict, match="held by pod-b"):
            await transition(s, running_exp.id, RUNNING, holder="pod-c")
        with pytest.raises(Conflict):
            await transition(s, running_exp.id, EVALUATING, holder="pod-c")


async def test_full_lifecycle_and_re_evaluate(session_factory, running_exp):
    async with session_factory() as s:
        await transition(s, running_exp.id, EVALUATING, holder="pod-a")
        done = await transition(s, running_exp.id, COMPLETED, holder="pod-a")
        again = await transition(s, running_exp.id, EVALUATING, holder="pod-b")
        await s.commit()
    assert done.status is COMPLETED and done.lease_holder is None
    assert again.status is EVALUATING and again.lease_holder == "pod-b"


async def test_expired_lease_can_be_taken_over(session_factory, running_exp):
    await _expire(session_factory, running_exp.id)
    async with session_factory() as s:
        exp = await transition(s, running_exp.id, EVALUATING, holder="pod-b")
        await s.commit()
    assert exp.lease_holder == "pod-b"


async def test_renew_lease_only_for_holder(session_factory, running_exp):
    await _expire(session_factory, running_exp.id)
    async with session_factory() as s:
        assert await renew_lease(s, running_exp.id, "pod-b") is False
        assert await renew_lease(s, running_exp.id, "pod-a") is True
        await s.commit()
        assert await stale_active(s, datetime.now(UTC)) == []


async def test_stale_active_lists_expired_only_and_reaper_fails_them(
    session_factory, world, running_exp
):
    async with session_factory() as s:
        live = await create_experiment(s, await new(s, holder="pod-b"))
        done = await create_experiment(s, await new(s, holder="pod-c"))
        await transition(s, done.id, FAILED, holder="pod-c", error="x")
        await s.commit()
    await _expire(session_factory, running_exp.id)
    async with session_factory() as s:
        stale = await stale_active(s, datetime.now(UTC))
        assert stale == [running_exp.id]
        assert await transition_stale(s, running_exp.id) is True
        await s.commit()
        reaped = await get_experiment(s, running_exp.id)
        assert await transition_stale(s, live.id) is False
    assert reaped.status is FAILED
    assert reaped.status_error == "runner pod-a stopped responding"


@pytest.mark.parametrize(
    ("dataset", "agent", "home"),
    [
        ("finance@dev", "ouragent@dev", "dev"),
        ("finance@dev", "myagent@akshat", "akshat"),
        ("finance@akshat", "ouragent@dev", "akshat"),
        ("finance@akshat", "myagent@akshat", "akshat"),
    ],
)
async def test_home_workspace_rule(session_factory, world, dataset, agent, home):
    async with session_factory() as s:
        exp = await create_experiment(s, await new(s, dataset=dataset, agent=agent))
    assert exp.workspace == home


async def test_number_is_per_dataset_sequence(session_factory, world):
    async with session_factory() as s:
        numbers = [
            (await create_experiment(s, await new(s, dataset=d))).number
            for d in ("finance@dev", "finance@dev", "finance@akshat", "finance@dev")
        ]
    assert numbers == [1, 2, 1, 3]


async def test_concurrent_creates_on_one_dataset_get_distinct_numbers(session_factory, world):
    async def one() -> int:
        async with session_factory() as s:
            exp = await create_experiment(s, await new(s))
            await s.commit()
            return exp.number

    numbers = await asyncio.gather(*(one() for _ in range(5)))
    assert sorted(numbers) == [1, 2, 3, 4, 5]


async def test_experiment_cases_freeze_version_and_overlays(session_factory, world):
    async with session_factory() as s:
        await sync(
            s,
            world.dev,
            world.admin_id,
            [],
            dataset="support-bot",
            overlays=(overlay("q-1", ["support-bot"]),),
        )
        exp = await create_experiment(s, await new(s, agent="support-bot@dev", cases=("q-1",)))
        await sync(s, world.dev, world.admin_id, [incoming("q-1", question="changed")])
        await sync(s, world.dev, world.admin_id, [], dataset="support-bot")
        await s.commit()
        [pinned] = (
            await s.scalars(select(ExperimentCase).where(ExperimentCase.experiment_id == exp.id))
        ).all()
    assert pinned.content_hash == incoming("q-1").content_hash
    assert pinned.effective_evaluators["custom"] == {"tone": {"strict": True}}


async def test_experiment_pins_the_versions_it_was_given(session_factory, world):
    async with session_factory() as s:
        req = await new(s)
        await sync(s, world.dev, world.admin_id, [incoming("q-1", question="changed")])
        exp = await create_experiment(s, req)
        await s.commit()
        [pinned] = (
            await s.scalars(select(ExperimentCase).where(ExperimentCase.experiment_id == exp.id))
        ).all()
    assert pinned.content_hash == incoming("q-1").content_hash


async def test_unknown_experiment(session_factory, world):
    async with session_factory() as s:
        with pytest.raises(UnknownExperiment):
            await get_experiment(s, 999)


async def test_list_experiments_filters(session_factory, world, running_exp):
    async with session_factory() as s:
        mine = await create_experiment(s, await new(s, dataset="finance@akshat"))
        await transition(s, mine.id, FAILED, holder="pod-a", error="x")
        await s.commit()
        assert [e.id for e in await list_experiments(s)] == [mine.id, running_exp.id]
        assert [e.id for e in await list_experiments(s, status=RUNNING)] == [running_exp.id]
        assert [e.id for e in await list_experiments(s, workspace_id=world.akshat_ws.id)] == [
            mine.id
        ]
