"""Aggregation of evaluation rows into the scorecard report.

A row's bucket is resolved when it is written (`bucket_for` plus the
exclusions below); bucket means are computed here. `answer_rubric_score` is the
`content` bucket's only member; every other name the rubric evaluator
publishes is a diagnostic on it and stays out of the bucket — recall =
coverage x accuracy identically, so taking all three would double-count every
missed rubric; the per-type rates partition the very rubrics the score
already counts; and citation is a separate axis whose denominator exists only
on cases that ask for sources.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable

from pydantic import BaseModel, Field

from evalkit.store.base import AggregateRows

GAUGE_PREFIX = "gauge_"

ANSWER_RUBRIC_ORDER: tuple[str, ...] = (
    "answer_rubric_score",
    "answer_rubric_coverage",
    "answer_rubric_accuracy",
    "answer_rubric_factual_retrieval",
    "answer_rubric_derived_computation",
    "answer_rubric_analysis_interpretation",
    "answer_rubric_citation",
)

ANSWER_RUBRIC_DIAGNOSTICS = frozenset(ANSWER_RUBRIC_ORDER) - {"answer_rubric_score"}

EXCLUDED_FROM_BUCKETS = ANSWER_RUBRIC_DIAGNOSTICS

BUCKET_ORDER: tuple[str, ...] = (
    "content",
    "trajectory",
    "presentation",
    "efficiency",
    "custom",
)

BUCKETS: dict[str, frozenset[str]] = {
    "content": frozenset({"answer_rubric_score"}),
    "trajectory": frozenset(
        {"trajectory_rubric_score", "tool_trajectory_match", "required_tools_called"}
    ),
    "presentation": frozenset({"presentation_judge"}),
    "efficiency": frozenset({"max_response_time"}),
}

_BUCKET_BY_EVALUATOR: dict[str, str] = {
    name: bucket for bucket, members in BUCKETS.items() for name in members
}

# One representative evaluator per bucket the inline score blocks show: every
# bucket with a single member. `custom` has no fixed member and is left out.
BUCKET_EVALUATOR = {
    bucket: next(iter(members)) for bucket, members in BUCKETS.items() if len(members) == 1
}
COST_GAUGE = "gauge_agent_cost_usd"


def is_gauge(name: str) -> bool:
    return name.startswith(GAUGE_PREFIX)


def gauge_places(name: str) -> int:
    """Display precision for a gauge value — dollar gauges need decimals,
    count/token gauges render as whole numbers."""
    return 4 if name.endswith("_cost_usd") else 0


def bucket_for(name: str, source: str = "builtin") -> str | None:
    if source.startswith("custom:"):
        return "custom"
    return _BUCKET_BY_EVALUATOR.get(name)


def order_evaluator_columns(present: Iterable[str]) -> list[str]:
    present = set(present)
    head = [name for name in ANSWER_RUBRIC_ORDER if name in present]
    tail = sorted(present.difference(head))
    return head + tail


class CaseRow(BaseModel):
    case_id: str
    scores: dict[str, float | None] = Field(default_factory=dict)
    explanations: dict[str, str | None] = Field(default_factory=dict)
    gauges: dict[str, float | None] = Field(default_factory=dict)
    errors: dict[str, str] = Field(default_factory=dict)
    run_errors: list[str] = Field(default_factory=list)
    repetitions: int = 1
    overall: float | None = None
    baseline_overall: float | None = None
    delta: float | None = None


class EvaluatorStat(BaseModel):
    name: str
    score: float | None = None
    cases: int = 0
    errors: int = 0
    bucket: str | None = None
    source: str = "builtin"


class GaugeStat(BaseModel):
    name: str
    mean: float | None = None
    median: float | None = None
    count: int = 0


class Report(BaseModel):
    experiment_id: str
    name: str
    dataset: str
    metadata: dict = Field(default_factory=dict)
    cases: list[CaseRow]
    evaluators: list[EvaluatorStat]
    buckets: dict[str, float | None]
    gauges: list[GaugeStat]
    evaluator_columns: list[str]
    baseline_id: str | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _aggregate_rows(agg: AggregateRows) -> dict[str, CaseRow]:
    rows = {
        case_id: CaseRow(case_id=case_id, repetitions=n)
        for case_id, n in agg.repetitions_seen.items()
    }
    bucketed: dict[str, list[float]] = {}
    for case_id, name, mean, explanation, bucket in agg.case_scores:
        rows[case_id].scores[name] = mean
        rows[case_id].explanations[name] = explanation
        if bucket is not None:
            bucketed.setdefault(case_id, []).append(mean)
    for case_id, name, mean in agg.case_gauges:
        rows[case_id].gauges[name] = mean
    for case_id, name, error in agg.case_errors:
        rows[case_id].errors[name] = error
    for case_id, error in agg.run_errors:
        rows[case_id].run_errors.append(error)
    for case_id, row in rows.items():
        row.overall = _mean(bucketed.get(case_id, []))
    return rows


def assemble_report(agg: AggregateRows, baseline: AggregateRows | None = None) -> Report:
    exp = agg.experiment
    return _report(
        Report(
            experiment_id=str(exp.id),
            name=f"#{exp.number}",
            dataset=exp.dataset,
            metadata={
                **exp.config.model_dump(mode="json"),
                "agent": exp.agent,
                "created_by": exp.created_by,
                "status": exp.status.value,
            },
            cases=[],
            evaluators=[],
            buckets={},
            gauges=[],
            evaluator_columns=[],
        ),
        _aggregate_rows(agg),
        agg.sources,
        {name: bucket for _, name, _, _, bucket in agg.case_scores},
        None if baseline is None else (str(baseline.experiment.id), _aggregate_rows(baseline)),
    )


def _report(
    header: Report,
    rows: dict[str, CaseRow],
    sources: dict[str, str],
    stored_buckets: dict[str, str | None],
    baseline: tuple[str, dict[str, CaseRow]] | None,
) -> Report:
    evaluator_names = {
        name
        for row in rows.values()
        for name in list(row.scores) + list(row.errors)
        if not is_gauge(name)
    }
    evaluators = []
    for name in order_evaluator_columns(evaluator_names):
        case_values = [
            value for row in rows.values() if (value := row.scores.get(name)) is not None
        ]
        evaluators.append(
            EvaluatorStat(
                name=name,
                score=_mean(case_values),
                cases=len(case_values),
                errors=sum(1 for row in rows.values() if name in row.errors),
                bucket=stored_buckets.get(name),
                source=sources.get(name, "builtin"),
            )
        )

    buckets: dict[str, float | None] = {}
    for bucket in BUCKET_ORDER:
        members = [
            stat.score
            for stat in evaluators
            if stat.bucket == bucket
            and stat.score is not None
            and stat.name not in EXCLUDED_FROM_BUCKETS
        ]
        buckets[bucket] = _mean(members)

    gauge_names = {name for row in rows.values() for name in row.gauges}
    gauges = []
    for name in sorted(gauge_names):
        values = [value for row in rows.values() if (value := row.gauges.get(name)) is not None]
        gauges.append(
            GaugeStat(
                name=name,
                mean=_mean(values),
                median=statistics.median(values) if values else None,
                count=len(values),
            )
        )

    baseline_id = None
    if baseline is not None:
        baseline_id, baseline_rows = baseline
        for case_id, row in rows.items():
            base = baseline_rows.get(case_id)
            if base is not None and base.overall is not None and row.overall is not None:
                row.baseline_overall = base.overall
                row.delta = round(row.overall - base.overall, 6)

    return header.model_copy(
        update={
            "cases": sorted(rows.values(), key=lambda r: r.case_id),
            "evaluators": evaluators,
            "buckets": buckets,
            "gauges": gauges,
            "evaluator_columns": order_evaluator_columns(evaluator_names),
            "baseline_id": baseline_id,
        }
    )
