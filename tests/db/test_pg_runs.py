from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from evalkit.contracts.enums import EvaluationSource, EvaluatorKind, RunStatus
from evalkit.db.experiments import create_experiment
from evalkit.db.models import AgentRun, Evaluation
from evalkit.db.runs import pending_runs, publish_evaluations, record_run, runs_to_score
from evalkit.store.base import AgentRunRecord, CaseRep, Conflict, EvaluationRow, InvalidRun
from tests.db.test_pg_experiments import new
from tests.identity import seed_agent
from tests.seed import World, count, incoming, seed_world, sync


@pytest.fixture
async def exp_id(session_factory) -> int:
    world: World = await seed_world(session_factory)
    await seed_agent(session_factory, "ouragent", "dev")
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1"), incoming("q-2")])
        exp = await create_experiment(s, await new(s, cases=("q-1", "q-2")))
        await s.commit()
    return exp.id


def attempt(harness_run_id: str, status: RunStatus, error: str | None = None) -> AgentRunRecord:
    return AgentRunRecord(
        harness_run_id=harness_run_id,
        status=status,
        answer="42" if status is RunStatus.COMPLETED else None,
        error=error,
        started_at=datetime.now(UTC),
    )


def score(name: str, producer: str = "answer_rubric", **kw) -> EvaluationRow:
    defaults = {"kind": EvaluatorKind.SCORE, "source": EvaluationSource.BUILTIN, "value": 1.0}
    return EvaluationRow(producer=producer, name=name, **(defaults | kw))


async def _evaluations(s, run_id: int) -> dict[str, Evaluation]:
    rows = await s.scalars(select(Evaluation).where(Evaluation.experiment_run_id == run_id))
    return {row.name: row for row in rows.all()}


async def test_retry_replaces_agent_run_and_keeps_both(session_factory, exp_id):
    async with session_factory() as s:
        first = await record_run(s, exp_id, "q-1", 1, attempt("h1", RunStatus.FAILED, "x"), None)
        retry = await record_run(s, exp_id, "q-1", 1, attempt("h2", RunStatus.COMPLETED), None)
        await s.commit()
    assert first.experiment_run_id == retry.experiment_run_id
    assert first.agent_run_id != retry.agent_run_id
    assert await count(session_factory, AgentRun) == 2


async def test_retry_after_a_scored_attempt_is_scored_again(session_factory, exp_id):
    async with session_factory() as s:
        timed_out = attempt("h1", RunStatus.TIMEOUT, "slow").model_copy(update={"answer": "old"})
        first = await record_run(s, exp_id, "q-1", 1, timed_out, None)
        await publish_evaluations(s, first.experiment_run_id, [score("answer_rubric_score")])
        await record_run(s, exp_id, "q-1", 1, attempt("h2", RunStatus.COMPLETED), None)
        [run] = [r for r in await runs_to_score(s, exp_id, force=False) if r.case.case_id == "q-1"]
    assert run.agent_run.harness_run_id == "h2"
    assert run.done == set()


async def test_completed_run_is_never_replaced(session_factory, exp_id):
    async with session_factory() as s:
        await record_run(s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None)
        with pytest.raises(Conflict, match="already has a completed run"):
            await record_run(s, exp_id, "q-1", 1, attempt("h2", RunStatus.FAILED, "late"), None)


async def test_reused_harness_run_id_is_a_conflict(session_factory, exp_id):
    async with session_factory() as s:
        await record_run(s, exp_id, "q-1", 1, attempt("same", RunStatus.COMPLETED), None)
        with pytest.raises(Conflict, match="same was already used"):
            await record_run(s, exp_id, "q-2", 1, attempt("same", RunStatus.COMPLETED), None)


async def test_writes_need_the_lease_and_a_pinned_case(session_factory, exp_id):
    async with session_factory() as s:
        with pytest.raises(Conflict, match="pod-b no longer holds its lease"):
            await record_run(
                s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None, holder="pod-b"
            )
        with pytest.raises(InvalidRun, match="q-9 is not pinned"):
            await record_run(s, exp_id, "q-9", 1, attempt("h1", RunStatus.COMPLETED), None)
        with pytest.raises(InvalidRun, match="outside 1..2"):
            await record_run(s, exp_id, "q-1", 3, attempt("h1", RunStatus.COMPLETED), None)
        ref = await record_run(
            s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None, holder="pod-a"
        )
        with pytest.raises(Conflict):
            await publish_evaluations(
                s, ref.experiment_run_id, [score("answer_rubric_score")], holder="pod-b"
            )


async def test_pending_runs_after_one_completed_and_one_failed(session_factory, exp_id):
    async with session_factory() as s:
        await record_run(s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None)
        await record_run(s, exp_id, "q-1", 2, attempt("h2", RunStatus.FAILED, "boom"), None)
        pending = await pending_runs(s, exp_id)
    assert pending == [
        CaseRep(case_id="q-1", repetition=2),
        CaseRep(case_id="q-2", repetition=1),
        CaseRep(case_id="q-2", repetition=2),
    ]


async def test_runs_to_score_skips_errored_runs_and_lists_done(session_factory, exp_id):
    async with session_factory() as s:
        ok = await record_run(s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None)
        await record_run(s, exp_id, "q-2", 1, attempt("h2", RunStatus.FAILED, "boom"), None)
        await publish_evaluations(
            s, ok.experiment_run_id, [score("answer_rubric_score"), score("x", error="judge down")]
        )
        runs = await runs_to_score(s, exp_id, force=False)
        forced = await runs_to_score(s, exp_id, force=True)
    [run] = runs
    assert (run.case.case_id, run.repetition, run.agent_run.answer) == ("q-1", 1, "42")
    assert run.done == {"answer_rubric_score"}
    assert forced[0].done == set()


async def test_success_deletes_the_producers_error_rows(session_factory, exp_id):
    async with session_factory() as s:
        ref = await record_run(s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None)
        await publish_evaluations(
            s, ref.experiment_run_id, [score("answer_rubric", error="judge timed out")]
        )
        await publish_evaluations(s, ref.experiment_run_id, [score("answer_rubric_score")])
        found = await _evaluations(s, ref.experiment_run_id)
    assert set(found) == {"answer_rubric_score"}


async def test_multi_name_producer_stores_one_row_per_name(session_factory, exp_id):
    names = ["answer_rubric_score", "answer_rubric_coverage", "answer_rubric_precision"]
    async with session_factory() as s:
        ref = await record_run(s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None)
        await publish_evaluations(s, ref.experiment_run_id, [score(n) for n in names])
        found = await _evaluations(s, ref.experiment_run_id)
    assert sorted(found) == sorted(names)
    assert {row.producer for row in found.values()} == {"answer_rubric"}


async def test_successful_row_survives_non_force_republish(session_factory, exp_id):
    async with session_factory() as s:
        ref = await record_run(s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None)
        run_id = ref.experiment_run_id
        await publish_evaluations(s, run_id, [score("answer_rubric_score", value=0.5)])
        await publish_evaluations(s, run_id, [score("answer_rubric_score", value=0.9)])
        kept = (await _evaluations(s, run_id))["answer_rubric_score"].value
        await publish_evaluations(s, run_id, [score("answer_rubric_score", value=0.9)], force=True)
        s.expire_all()
        forced = (await _evaluations(s, run_id))["answer_rubric_score"].value
    assert (kept, forced) == (0.5, 0.9)


async def test_bucket_is_resolved_at_write_time(session_factory, exp_id):
    rows = [
        score("answer_rubric_score"),
        score("answer_rubric_coverage"),
        score("tone", producer="tone", source=EvaluationSource.REMOTE),
        score("tokens", producer="tokens", kind=EvaluatorKind.GAUGE, value=120),
        score("broken", producer="broken", error="boom", value=None),
    ]
    async with session_factory() as s:
        ref = await record_run(s, exp_id, "q-1", 1, attempt("h1", RunStatus.COMPLETED), None)
        await publish_evaluations(s, ref.experiment_run_id, rows)
        found = await _evaluations(s, ref.experiment_run_id)
    assert {name: row.bucket for name, row in found.items()} == {
        "answer_rubric_score": "content",
        "answer_rubric_coverage": None,
        "tone": "custom",
        "tokens": None,
        "broken": None,
    }
