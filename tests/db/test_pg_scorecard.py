from datetime import UTC, datetime

import pytest

from evalkit.contracts.enums import EvaluationSource, EvaluatorKind, RunStatus
from evalkit.core.scorecard import assemble_report
from evalkit.db.cases import get_dataset
from evalkit.db.experiments import create_experiment
from evalkit.db.runs import publish_evaluations, record_run, runs_to_score
from evalkit.db.scorecard import (
    RunFacts,
    aggregate,
    case_history,
    case_state,
    case_states,
    list_scores,
    progress,
)
from evalkit.store.base import (
    AgentRunRecord,
    EvaluationRow,
    Progress,
)
from tests.db.test_pg_experiments import new
from tests.identity import seed_agent
from tests.seed import incoming, seed_world, sync

Score = tuple[str, str, EvaluationSource, EvaluatorKind, float | None, str | None]


def ev(
    name, value=None, *, producer=None, source=EvaluationSource.BUILTIN, gauge=False, error=None
):
    return (
        name,
        producer or name,
        source,
        EvaluatorKind.GAUGE if gauge else EvaluatorKind.SCORE,
        value,
        error,
    )


CURRENT: dict[tuple[str, int], tuple[str | None, list[list[Score]]]] = {
    ("q-1", 1): (
        None,
        [
            [ev("presentation_judge", error="judge timed out")],
            [
                ev("answer_rubric_score", 0.8, producer="answer_rubric"),
                ev("answer_rubric_coverage", 1.0, producer="answer_rubric"),
                ev("presentation_judge", 0.5),
                ev("tone", 1.0, source=EvaluationSource.REMOTE),
                ev("gauge_agent_cost_usd", 0.02, gauge=True),
            ],
        ],
    ),
    ("q-1", 2): (
        None,
        [
            [
                ev("answer_rubric_score", 0.4, producer="answer_rubric"),
                ev("answer_rubric_coverage", 0.5, producer="answer_rubric"),
                ev("tone", 0.0, source=EvaluationSource.REMOTE),
                ev("gauge_agent_cost_usd", 0.04, gauge=True),
            ]
        ],
    ),
    ("q-2", 1): (
        None,
        [
            [
                ev("answer_rubric_score", 1.0, producer="answer_rubric"),
                ev("broken", error="500 from evaluator"),
                ev("gauge_agent_cost_usd", 0.01, gauge=True),
            ]
        ],
    ),
    ("q-2", 2): ("harness crashed", []),
}
BASELINE = {
    ("q-1", 1): (None, [[ev("answer_rubric_score", 0.2, producer="answer_rubric")]]),
    ("q-2", 1): (None, [[ev("answer_rubric_score", 1.0, producer="answer_rubric")]]),
}


async def _store(s, exp_id, runs) -> None:
    for (case_id, rep), (error, batches) in runs.items():
        ref = await record_run(
            s,
            exp_id,
            case_id,
            rep,
            AgentRunRecord(
                harness_run_id=f"{exp_id}-{case_id}-{rep}",
                status=RunStatus.FAILED if error else RunStatus.COMPLETED,
                answer=None if error else "42",
                error=error,
                started_at=datetime.now(UTC),
            ),
            None,
        )
        for batch in batches:
            await publish_evaluations(
                s,
                ref.experiment_run_id,
                [
                    EvaluationRow(
                        producer=producer, name=name, kind=kind, source=source, value=v, error=e
                    )
                    for name, producer, source, kind, v, e in batch
                ],
            )


@pytest.fixture
async def experiments(session_factory) -> tuple[int, int]:
    world = await seed_world(session_factory)
    await seed_agent(session_factory, "ouragent", "dev")
    async with session_factory() as s:
        await sync(
            s, world.dev, world.admin_id, [incoming("q-1"), incoming("q-2"), incoming("q-3")]
        )
        baseline = await create_experiment(s, await new(s, cases=("q-1", "q-2")))
        current = await create_experiment(s, await new(s, cases=("q-1", "q-2", "q-3")))
        await _store(s, baseline.id, BASELINE)
        await _store(s, current.id, CURRENT)
        await s.commit()
    return current.id, baseline.id


def stat(name, score, cases, errors, bucket, source="builtin") -> dict:
    return {
        "name": name,
        "score": score,
        "cases": cases,
        "errors": errors,
        "bucket": bucket,
        "source": source,
    }


EXPECTED = {
    "buckets": {
        "content": pytest.approx(0.8),
        "trajectory": None,
        "presentation": 0.5,
        "efficiency": None,
        "custom": 0.5,
    },
    "cases": [
        {
            "case_id": "q-1",
            "scores": {
                "answer_rubric_coverage": 0.75,
                "answer_rubric_score": pytest.approx(0.6),
                "presentation_judge": 0.5,
                "tone": 0.5,
            },
            "gauges": {"gauge_agent_cost_usd": pytest.approx(0.03)},
            "errors": {},
            "run_errors": [],
            "errored_runs": 0,
            "repetitions": 2,
            "overall": pytest.approx(1.6 / 3),
            "baseline_overall": 0.2,
            "delta": 0.333333,
        },
        {
            "case_id": "q-2",
            "scores": {"answer_rubric_score": 1.0},
            "gauges": {"gauge_agent_cost_usd": 0.01},
            "errors": {"broken": "500 from evaluator"},
            "run_errors": ["harness crashed"],
            "errored_runs": 1,
            "repetitions": 2,
            "overall": 1.0,
            "baseline_overall": 1.0,
            "delta": 0.0,
        },
    ],
    "evaluator_columns": [
        "answer_rubric_score",
        "answer_rubric_coverage",
        "broken",
        "presentation_judge",
        "tone",
    ],
    "evaluators": [
        stat("answer_rubric_score", pytest.approx(0.8), 2, 0, "content"),
        stat("answer_rubric_coverage", 0.75, 1, 0, None),
        stat("broken", None, 0, 1, None),
        stat("presentation_judge", 0.5, 1, 0, "presentation"),
        stat("tone", 0.5, 1, 0, "custom", "custom:tone"),
    ],
    "gauges": [
        {
            "name": "gauge_agent_cost_usd",
            "mean": pytest.approx(0.02),
            "median": pytest.approx(0.02),
            "count": 2,
        }
    ],
}


def _body(report) -> dict:
    return report.model_dump(
        include={"cases", "evaluators", "buckets", "gauges", "evaluator_columns"},
        exclude={"cases": {"__all__": {"explanations"}}},
    )


async def test_sql_report_values(session_factory, experiments):
    current, baseline = experiments
    async with session_factory() as s:
        new_report = assemble_report(await aggregate(s, current), await aggregate(s, baseline))
    assert _body(new_report) == EXPECTED
    assert [sorted(row.explanations) for row in new_report.cases] == [
        sorted(row.scores) for row in new_report.cases
    ]
    assert new_report.baseline_id == str(baseline)
    by_case = {row.case_id: row for row in new_report.cases}
    assert by_case["q-1"].repetitions == 2 and by_case["q-1"].errors == {}
    assert by_case["q-2"].errors == {"broken": "500 from evaluator"}
    assert by_case["q-2"].run_errors == ["harness crashed"]
    assert new_report.buckets["custom"] == 0.5
    assert (new_report.errored_cases, new_report.partly_errored_cases) == (0, 1)
    assert new_report.selected_cases == 3
    assert new_report.bucket_cases == {"content": 2, "presentation": 1, "custom": 1}


async def test_progress_and_case_states(session_factory, experiments):
    current, baseline = experiments
    async with session_factory() as s:
        states = await case_states(s, current)
        assert states == {"q-1": "evaluated", "q-2": "evaluated", "q-3": "pending"}
        assert await progress(s, current) == Progress(
            total=3, ran=2, evaluated=2, errored=0, partly_errored=1
        )


async def test_a_case_is_errored_only_when_every_repetition_errored(session_factory, experiments):
    current, _ = experiments
    async with session_factory() as s:
        await _store(s, current, {("q-3", 1): ("boom", []), ("q-3", 2): ("boom again", [])})
        report = assemble_report(await aggregate(s, current))
        assert (report.errored_cases, report.partly_errored_cases) == (1, 1)
        assert report.bucket_cases["content"] == 2 and report.selected_cases == 3
        assert await progress(s, current) == Progress(
            total=3, ran=3, evaluated=3, errored=1, partly_errored=1
        )


def run_record(status: RunStatus, **fields) -> AgentRunRecord:
    return AgentRunRecord(
        harness_run_id=f"{status.value}-{sorted(fields)}",
        status=status,
        started_at=datetime.now(UTC),
        **fields,
    )


@pytest.mark.parametrize(
    ("run", "errored"),
    [
        (run_record(RunStatus.COMPLETED, answer="42"), False),
        (run_record(RunStatus.COMPLETED, answer="partial", error="tool failed once"), False),
        (run_record(RunStatus.COMPLETED, output={"total": 1}, error="warning"), False),
        (run_record(RunStatus.FAILED, output={"total": 1}, error="late crash"), False),
        (run_record(RunStatus.COMPLETED), True),
        (run_record(RunStatus.FAILED), True),
        (run_record(RunStatus.TIMEOUT), True),
        (run_record(RunStatus.CANCELLED), True),
        (run_record(RunStatus.FAILED, answer="half", error="crashed"), True),
        (run_record(RunStatus.RUNNING), False),
    ],
)
async def test_errored_follows_the_run_status_and_answer(
    session_factory, experiments, run, errored
):
    current, _ = experiments
    async with session_factory() as s:
        await record_run(s, current, "q-3", 1, run, None)
        report = assemble_report(await aggregate(s, current))
        scored = {r.case.case_id for r in await runs_to_score(s, current, force=True)}
        counted = (await progress(s, current)).errored
    assert (report.errored_cases, counted) == (int(errored), int(errored))
    finished = run.status is not RunStatus.RUNNING
    assert ("q-3" in scored) == (finished and not errored)


DONE, FAILED_WITH_OUTPUT, FAILED_NO_OUTPUT, LIVE = (
    RunFacts(finished=True, scorable=True, evaluated=True),
    RunFacts(finished=True, scorable=True, evaluated=False),
    RunFacts(finished=True, scorable=False, evaluated=False),
    RunFacts(finished=False, scorable=True, evaluated=False),
)


@pytest.mark.parametrize(
    ("repetitions", "runs", "state"),
    [
        (2, [], "pending"),
        (2, [LIVE], "pending"),
        (3, [DONE], "ran"),
        (2, [DONE, LIVE], "ran"),
        (2, [DONE, FAILED_WITH_OUTPUT], "ran"),
        (2, [DONE, FAILED_NO_OUTPUT], "evaluated"),
        (2, [FAILED_NO_OUTPUT, FAILED_NO_OUTPUT], "evaluated"),
        (2, [DONE, DONE], "evaluated"),
    ],
)
def test_case_state_table(repetitions, runs, state):
    assert case_state(repetitions, runs) == state


async def test_list_scores_uses_the_scorecard_bucket_rules(session_factory, experiments):
    current, baseline = experiments
    async with session_factory() as s:
        scores = await list_scores(s, [current, baseline])
        report = assemble_report(await aggregate(s, current))
    assert scores[current] == {k: v for k, v in report.buckets.items() if v is not None}
    assert scores[baseline] == {"content": pytest.approx(0.6)}


async def test_case_history_newest_first(session_factory, experiments):
    current, baseline = experiments
    async with session_factory() as s:
        dataset_id = (await get_dataset(s, "default", "dev", "finance")).id
        history = await case_history(s, dataset_id, ["q-1"], limit=10)
        everything = await case_history(s, dataset_id, None, limit=10)
    [newest, oldest] = history["q-1"]
    assert (newest.experiment_id, oldest.experiment_id) == (current, baseline)
    assert newest.scores["content"] == pytest.approx(0.6)
    assert newest.scores["presentation"] == 0.5
    assert newest.cost_usd == pytest.approx(0.03)
    assert oldest.scores["content"] == pytest.approx(0.2) and oldest.cost_usd is None
    assert sorted(everything) == ["q-1", "q-2"]
