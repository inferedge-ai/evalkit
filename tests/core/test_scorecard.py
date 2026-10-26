from datetime import UTC, datetime

from evalkit.contracts.enums import ExperimentStatus, TrajectorySource
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.scorecard import (
    EXCLUDED_FROM_BUCKETS,
    assemble_report,
    bucket_for,
    gauge_places,
)
from evalkit.store.base import (
    AggregateRows,
    Experiment,
    ExperimentConfig,
    ExperimentSnapshot,
    ScoreRow,
    Selection,
)

Run = tuple[str, int, list[ScoreRow], str | None]


def experiment(exp_id: int) -> Experiment:
    return Experiment(
        id=exp_id,
        number=exp_id,
        workspace="canonical",
        dataset="finance@canonical",
        agent="fake@canonical",
        agent_id=1,
        created_by="admin",
        status=ExperimentStatus.COMPLETED,
        status_error=None,
        lease_holder=None,
        lease_expires_at=None,
        repetitions=1,
        config=ExperimentConfig(
            judge=JudgeSettings(base_url="http://j", model="m"),
            selection=Selection(all=True),
            snapshot=ExperimentSnapshot(
                capabilities={}, trajectory_source=TrajectorySource.ATIF, trajectory_wait_seconds=0
            ),
        ),
        started_at=datetime(2026, 10, 13, tzinfo=UTC),
        ended_at=None,
    )


def _bucket(row: ScoreRow) -> str | None:
    if row.error is not None or row.kind == "gauge" or row.name in EXCLUDED_FROM_BUCKETS:
        return None
    return bucket_for(row.name, row.source)


def detail(runs: list[Run], exp_id: int = 1) -> AggregateRows:
    scores: dict[tuple[str, str], list[float]] = {}
    buckets: dict[tuple[str, str], str | None] = {}
    gauges: dict[tuple[str, str], list[float]] = {}
    errors: dict[tuple[str, str], str] = {}
    reps: dict[str, int] = {}
    sources: dict[str, str] = {}
    run_errors: list[tuple[str, str]] = []
    for case_id, _, rows, error in runs:
        reps[case_id] = reps.get(case_id, 0) + 1
        if error:
            run_errors.append((case_id, error))
        for row in rows:
            sources[row.name] = row.source
            if row.error is not None:
                errors[(case_id, row.name)] = row.error
            elif row.score is not None and row.kind == "gauge":
                gauges.setdefault((case_id, row.name), []).append(row.score)
            elif row.score is not None:
                scores.setdefault((case_id, row.name), []).append(row.score)
                buckets[(case_id, row.name)] = _bucket(row)
    return AggregateRows(
        experiment=experiment(exp_id),
        case_scores=[
            (c, n, sum(v) / len(v), None, buckets[(c, n)]) for (c, n), v in scores.items()
        ],
        case_gauges=[(c, n, sum(v) / len(v)) for (c, n), v in gauges.items()],
        case_errors=[(c, n, e) for (c, n), e in errors.items()],
        run_errors=run_errors,
        repetitions_seen=reps,
        sources=sources,
    )


def run(case: str, rep: int, scores: list[ScoreRow], error: str | None = None) -> Run:
    return (case, rep, scores, error)


def s(name: str, score: float | None, **kw) -> ScoreRow:
    return ScoreRow(name=name, score=score, **kw)


def test_repetition_mean_and_none_exclusion():
    report = assemble_report(
        detail(
            [
                run("case-1", 1, [s("answer_rubric_score", 0.8)]),
                run("case-1", 2, [s("answer_rubric_score", 0.4)]),
                run("case-1", 3, [s("answer_rubric_score", None, label="skip")]),
            ]
        )
    )
    row = report.cases[0]
    assert abs(row.scores["answer_rubric_score"] - 0.6) < 1e-9
    assert row.repetitions == 3


def test_bucket_math_and_diagnostics_excluded():
    report = assemble_report(
        detail(
            [
                run(
                    "case-1",
                    1,
                    [
                        s("answer_rubric_score", 0.5),
                        s("answer_rubric_coverage", 1.0),
                        s("trajectory_rubric_score", 1.0),
                        s("presentation_judge", 0.75),
                        s("max_response_time", 1.0),
                        s("required_tool_calls", 0.0, source="custom:required_tool_calls"),
                        s("gauge_duration_seconds", 12.0, kind="gauge", label="info"),
                    ],
                )
            ]
        )
    )
    assert report.buckets["content"] == 0.5
    assert report.buckets["trajectory"] == 1.0
    assert report.buckets["presentation"] == 0.75
    assert report.buckets["efficiency"] == 1.0
    assert report.buckets["custom"] == 0.0
    stats = {e.name: e for e in report.evaluators}
    assert stats["answer_rubric_coverage"].bucket is None
    assert stats["required_tool_calls"].bucket == "custom"
    gauges = {g.name: g for g in report.gauges}
    assert gauges["gauge_duration_seconds"].mean == 12.0
    assert report.evaluator_columns[0] == "answer_rubric_score"


def test_baseline_delta_by_case():
    current = detail(
        [
            run("case-1", 1, [s("answer_rubric_score", 0.9)]),
            run("case-2", 1, [s("answer_rubric_score", 0.4)]),
        ]
    )
    baseline = detail(
        [
            run("case-1", 1, [s("answer_rubric_score", 0.5)]),
            run("case-2", 1, [s("answer_rubric_score", 0.8)]),
        ],
        exp_id=7,
    )
    report = assemble_report(current, baseline)
    by_case = {row.case_id: row for row in report.cases}
    assert by_case["case-1"].delta == 0.4
    assert by_case["case-2"].delta == -0.4
    assert report.baseline_id == "7"


def test_errored_scores_surface():
    report = assemble_report(
        detail([run("case-1", 1, [s("broken_eval", None, label="error", error="timeout")])])
    )
    assert report.cases[0].errors == {"broken_eval": "timeout"}
    stat = next(stat for stat in report.evaluators if stat.name == "broken_eval")
    assert (stat.cases, stat.errors) == (0, 1)


def test_gauge_places():
    assert gauge_places("gauge_agent_cost_usd") == 4
    assert gauge_places("gauge_input_tokens") == 0


def test_tool_trajectory_evaluators_join_trajectory_bucket():
    report = assemble_report(
        detail(
            [
                run(
                    "case-1",
                    1,
                    [
                        s("trajectory_rubric_score", 1.0),
                        s("tool_trajectory_match", 0.5),
                        s("required_tools_called", 0.0),
                    ],
                )
            ]
        )
    )
    assert report.buckets["trajectory"] == 0.5
    stats = {e.name: e for e in report.evaluators}
    assert stats["tool_trajectory_match"].bucket == "trajectory"
    assert stats["required_tools_called"].bucket == "trajectory"
