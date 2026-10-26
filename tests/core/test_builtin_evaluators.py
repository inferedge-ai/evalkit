import json

import pytest

from evalkit.contracts.harness import Capabilities
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.evaluators import presentation, rubrics
from evalkit.core.evaluators.base import BUILTIN_EVALUATORS, EvalContext, execute
from evalkit.core.judge import JudgeUsage
from evalkit.store.base import CaseView

JUDGE = JudgeSettings(base_url="http://j", model="m")


def ctx(wall_ms: int = 1000) -> EvalContext:
    case = CaseView(case_ref=1, case_id="c1", content_hash="h", input={"question": "q"})
    return EvalContext(
        case=case, answer="the answer", trajectory_view=None, wall_ms=wall_ms, judge_cfg=JUDGE
    )


def stub_judge(output):
    async def fake_call_judge(cfg, **kwargs):
        return output, JudgeUsage(input_tokens=10, output_tokens=5)

    return fake_call_judge


RUBRIC_CONFIG = {
    "rubrics": [
        {"rubric": "States revenue of $1.2B.", "type": "factual_retrieval", "source": "10-K"},
        {"rubric": "Computes CAGR of 5%.", "type": "derived_computation"},
        {"rubric": "Interprets the trend.", "type": "analysis_interpretation"},
        {"rubric": "Ranks the segments.", "type": "analysis_interpretation"},
    ]
}


async def test_rubric_aggregation_math(monkeypatch):
    verdicts = rubrics.RubricJudgeOutput(
        verdicts=[
            rubrics.RubricVerdict(
                index=1,
                verdict="satisfied",
                source=rubrics.SourceVerdict(verdict="incorrect"),
            ),
            rubrics.RubricVerdict(index=2, verdict="incorrect", note="gives 4%", source=None),
            rubrics.RubricVerdict(index=3, verdict="absent", note="no trend", source=None),
            rubrics.RubricVerdict(index=4, verdict="satisfied", source=None),
        ]
    )
    monkeypatch.setattr(rubrics, "call_judge", stub_judge(verdicts))
    context = ctx()
    rows = await rubrics.answer_rubrics(context, RUBRIC_CONFIG)
    by_name = {r.name: r for r in rows}
    assert by_name["answer_rubric_score"].score == 2 / 4
    assert by_name["answer_rubric_coverage"].score == 3 / 4
    assert by_name["answer_rubric_accuracy"].score == 2 / 3
    assert by_name["answer_rubric_factual_retrieval"].score == 1.0
    assert by_name["answer_rubric_derived_computation"].score == 0.0
    assert by_name["answer_rubric_analysis_interpretation"].score == 1 / 2
    assert by_name["answer_rubric_citation"].score == 0.0
    payload = json.loads(by_name["answer_rubric_score"].explanation)
    assert payload["satisfied"] == 2 and payload["total"] == 4
    assert context.judge_usage.input_tokens == 10


async def test_rubric_gap_retries_once(monkeypatch):
    calls = []

    async def flaky(cfg, **kwargs):
        calls.append(kwargs["user_prompt"])
        if len(calls) == 1:
            return (
                rubrics.RubricJudgeOutput(
                    verdicts=[rubrics.RubricVerdict(index=1, verdict="satisfied", source=None)]
                ),
                JudgeUsage(),
            )
        return (
            rubrics.RubricJudgeOutput(
                verdicts=[
                    rubrics.RubricVerdict(index=1, verdict="satisfied", source=None),
                    rubrics.RubricVerdict(index=2, verdict="satisfied", source=None),
                ]
            ),
            JudgeUsage(),
        )

    monkeypatch.setattr(rubrics, "call_judge", flaky)
    config = {
        "rubrics": [
            {"rubric": "A.", "type": "derived_computation"},
            {"rubric": "B.", "type": "derived_computation"},
        ]
    }
    rows = await rubrics.answer_rubrics(ctx(), config)
    assert len(calls) == 2
    assert "did not return" in calls[1]
    assert {r.name: r.score for r in rows}["answer_rubric_score"] == 1.0


async def test_presentation_points_weighting(monkeypatch):
    verdicts = presentation.PresentationJudgeOutput(
        verdicts=[
            presentation.PresentationVerdict(index=1, met=True, evidence="shown"),
            presentation.PresentationVerdict(index=2, met=False, evidence="not found"),
        ]
    )
    monkeypatch.setattr(presentation, "call_judge", stub_judge(verdicts))
    config = {
        "rubrics": [
            {"rubric": "Shows units.", "points": 3},
            {"forbid": "Restates the series.", "points": 1},
        ]
    }
    rows = await presentation.presentation_judge(ctx(), config)
    assert rows[0].score == 3 / 4


async def test_max_response_time_pass_fail():
    evaluator = BUILTIN_EVALUATORS["max_response_time"]
    caps = Capabilities.model_validate({"agent": "x"})
    rows = await execute(evaluator, ctx(wall_ms=5000), {"max_seconds": 10}, caps)
    assert rows[0].score == 1.0 and rows[0].label == "pass"
    rows = await execute(evaluator, ctx(wall_ms=15000), {"max_seconds": 10}, caps)
    assert rows[0].score == 0.0 and rows[0].label == "fail"


def test_rubric_prompt_carries_question_and_attribution_guidance():
    cfg = rubrics.AnswerRubrics.model_validate(RUBRIC_CONFIG)
    prompt = rubrics.user_prompt("What was FY24 revenue?", cfg.rubrics, "the answer")
    assert "QUESTION\nWhat was FY24 revenue?\n" in prompt
    assert prompt.index("QUESTION") < prompt.index("RUBRICS") < prompt.index("ANSWER")
    assert "anywhere in the answer" in prompt
    assert "prefer the table over the prose" not in prompt


async def test_rubric_missing_note_on_failed_verdict_retries(monkeypatch):
    calls = []

    async def flaky(cfg, **kwargs):
        calls.append(kwargs["user_prompt"])
        note = "" if len(calls) == 1 else "the answer gives 4%, not 5%"
        return (
            rubrics.RubricJudgeOutput(
                verdicts=[
                    rubrics.RubricVerdict(index=1, verdict="satisfied", source=None),
                    rubrics.RubricVerdict(index=2, verdict="incorrect", note=note, source=None),
                ]
            ),
            JudgeUsage(),
        )

    monkeypatch.setattr(rubrics, "call_judge", flaky)
    config = {
        "rubrics": [
            {"rubric": "A.", "type": "derived_computation"},
            {"rubric": "B.", "type": "derived_computation"},
        ]
    }
    rows = await rubrics.answer_rubrics(ctx(), config)
    assert len(calls) == 2
    assert "a note on non-satisfied rubrics [2]" in calls[1]
    assert {r.name: r.score for r in rows}["answer_rubric_score"] == 0.5


async def test_rubric_missing_note_twice_raises(monkeypatch):
    async def never_notes(cfg, **kwargs):
        return (
            rubrics.RubricJudgeOutput(
                verdicts=[rubrics.RubricVerdict(index=1, verdict="absent", source=None)]
            ),
            JudgeUsage(),
        )

    monkeypatch.setattr(rubrics, "call_judge", never_notes)
    config = {"rubrics": [{"rubric": "A.", "type": "derived_computation"}]}
    with pytest.raises(rubrics.IncompleteVerdicts):
        await rubrics.answer_rubrics(ctx(), config)
