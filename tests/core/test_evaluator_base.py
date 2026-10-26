from evalkit.contracts.harness import Capabilities
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.evaluators.base import (
    BUILTIN_EVALUATORS,
    BUILTIN_GAUGES,
    EvalContext,
    builtin_evaluator,
    builtin_gauge,
    execute,
    gate,
    label_from_score,
)
from evalkit.store.base import CaseView, ScoreRow

JUDGE = JudgeSettings(base_url="http://j", model="m")


def ctx() -> EvalContext:
    case = CaseView(case_ref=1, case_id="c1", content_hash="h", input={"question": "q"})
    return EvalContext(case=case, answer="a", trajectory_view=None, wall_ms=100, judge_cfg=JUDGE)


def caps(**provides) -> Capabilities:
    return Capabilities.model_validate({"agent": "x", "provides": provides})


@builtin_evaluator("fake_two_score", requires={"trajectory"})
async def fake_two_score(context, config):
    return [
        ScoreRow(name="fake_two_score", score=1.0),
        ScoreRow(name="fake_two_score_sub", score=0.4),
    ]


@builtin_gauge("gauge_fake", requires={"token_usage"})
async def gauge_fake(context, config):
    return [ScoreRow(name="gauge_fake", score=42.0)]


def test_registration():
    assert "fake_two_score" in BUILTIN_EVALUATORS
    assert BUILTIN_EVALUATORS["fake_two_score"].requires == frozenset({"trajectory"})
    assert BUILTIN_GAUGES["gauge_fake"].kind == "gauge"


def test_gate():
    assert gate(BUILTIN_EVALUATORS["fake_two_score"], caps(trajectory=True))
    assert not gate(BUILTIN_EVALUATORS["fake_two_score"], caps(trajectory=False))


async def test_gate_false_neutral_skip():
    rows = await execute(BUILTIN_EVALUATORS["fake_two_score"], ctx(), {}, caps())
    assert len(rows) == 1
    assert rows[0].score is None and rows[0].label == "skip"
    assert "trajectory" in rows[0].explanation


async def test_two_score_evaluator_with_labels():
    rows = await execute(BUILTIN_EVALUATORS["fake_two_score"], ctx(), {}, caps(trajectory=True))
    assert [r.label for r in rows] == ["pass", "fail"]


async def test_gauge_rows_get_info_label():
    rows = await execute(BUILTIN_GAUGES["gauge_fake"], ctx(), {}, caps(token_usage=True))
    assert rows[0].kind == "gauge" and rows[0].label == "info"


def test_label_bands():
    assert label_from_score(0.8) == "pass"
    assert label_from_score(0.5) == "warn"
    assert label_from_score(0.49) == "fail"


def test_emits_defaults_to_name():
    assert BUILTIN_EVALUATORS["fake_two_score"].emits == ("fake_two_score",)


@builtin_evaluator("fake_multi", emits=("fake_multi", "fake_multi_extra"))
async def fake_multi(context, config):
    return [ScoreRow(name="fake_multi", score=1.0), ScoreRow(name="fake_multi_extra", score=1.0)]


def test_emits_declared():
    assert BUILTIN_EVALUATORS["fake_multi"].emits == ("fake_multi", "fake_multi_extra")
