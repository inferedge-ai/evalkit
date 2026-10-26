import json

import pytest

from evalkit.contracts.atif import validate_trajectory
from evalkit.contracts.harness import Capabilities
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.evaluators import trajectory_rubrics as tr
from evalkit.core.evaluators.base import BUILTIN_EVALUATORS, EvalContext, execute
from evalkit.core.judge import JudgeUsage
from evalkit.core.trajectory import TrajectoryView
from evalkit.store.base import CaseView

JUDGE = JudgeSettings(base_url="http://j", model="m")

RUBRICS = [
    "Retrieves FY2024 Platform revenue as $3,522,776,000.",
    "Retrieves FY2023 Platform revenue as $2,994,105,000.",
    "Calculates the margin change as 102 bps.",
]


def view(results: list[str]) -> TrajectoryView:
    steps = []
    for i, text in enumerate(results, 1):
        steps.append(
            {
                "step_id": i,
                "source": "agent",
                "tool_calls": [
                    {"tool_call_id": f"c{i}", "function_name": "fetch", "arguments": {}}
                ],
                "observation": {"results": [{"source_call_id": f"c{i}", "content": text}]},
            }
        )
    return TrajectoryView(validate_trajectory({"schema_version": "ATIF-v1.8", "steps": steps}))


def ctx(results: list[str] | None, answer: str = "102 bps") -> EvalContext:
    case = CaseView(
        case_ref=1, case_id="c1", content_hash="h", input={"question": "Margin uplift?"}
    )
    return EvalContext(
        case=case,
        answer=answer,
        trajectory_view=view(results) if results is not None else None,
        wall_ms=1,
        judge_cfg=JUDGE,
    )


def output(*satisfied: bool, evidence: str = "main:1") -> tr.TrajectoryJudgeOutput:
    return tr.TrajectoryJudgeOutput(
        verdicts=[
            tr.TrajectoryVerdict(index=i, satisfied=s, evidence=evidence if s else "")
            for i, s in enumerate(satisfied, 1)
        ]
    )


# --- prompt


def test_prompt_carries_question_rubrics_trajectory_then_answer():
    prompt = tr.user_prompt("Margin uplift?", RUBRICS, '{"steps":[]}', "the answer")
    assert "Margin uplift?" in prompt
    assert "1. Retrieves FY2024 Platform revenue as $3,522,776,000." in prompt
    assert "3. Calculates the margin change as 102 bps." in prompt
    assert "TRAJECTORY (ATIF JSON)" in prompt
    assert "main:4-5" in prompt
    assert prompt.index('{"steps":[]}') < prompt.index("the answer")


def test_trajectory_json_is_compact_atif():
    text = tr.trajectory_json(view(["rev 2,994,105,000"]).trajectory)
    assert json.loads(text)["schema_version"] == "ATIF-v1.8"
    assert '"source_call_id":"c1"' in text
    assert ": " not in text


# --- evaluator


async def test_one_call_over_the_full_trajectory(monkeypatch):
    calls = []

    async def fake(cfg, **kwargs):
        calls.append(kwargs["user_prompt"])
        return output(True, False, True), JudgeUsage(input_tokens=10, output_tokens=2)

    monkeypatch.setattr(tr, "call_judge", fake)
    big = "3,522,776,000 " * 2000
    rows = await tr.trajectory_rubrics(ctx([big, "rev 2,994,105,000"]), {"rubrics": RUBRICS})
    assert len(calls) == 1
    assert calls[0].count("3,522,776,000") >= 2000
    assert "…[truncated]" not in calls[0]
    assert '"source_call_id":"c2"' in calls[0]
    assert "[tool]" not in calls[0]
    [row] = rows
    assert row.name == "trajectory_rubric_score"
    assert row.score == pytest.approx(2 / 3)
    detail = json.loads(row.explanation)
    assert (detail["satisfied"], detail["total"]) == (2, 3)
    assert detail["verdicts"][1] == {
        "index": 2,
        "rubric": RUBRICS[1],
        "verdict": "unsatisfied",
        "evidence": "",
        "note": "",
    }
    assert detail["verdicts"][0]["evidence"] == "main:1"


async def test_gap_retries_once_then_raises(monkeypatch):
    attempts = []

    async def flaky(cfg, **kwargs):
        attempts.append(kwargs["user_prompt"])
        return output(True), JudgeUsage()

    monkeypatch.setattr(tr, "call_judge", flaky)
    with pytest.raises(tr.IncompleteVerdicts):
        await tr.trajectory_rubrics(ctx(["x"]), {"rubrics": RUBRICS})
    assert len(attempts) == 2
    assert "did not return" in attempts[1]


async def test_gap_retry_recovers(monkeypatch):
    n = 0

    async def flaky(cfg, **kwargs):
        nonlocal n
        n += 1
        return (output(True) if n == 1 else output(True, True, True)), JudgeUsage()

    monkeypatch.setattr(tr, "call_judge", flaky)
    [row] = await tr.trajectory_rubrics(ctx(["x"]), {"rubrics": RUBRICS})
    assert row.score == 1.0


async def test_skips_without_trajectory():
    [row] = await tr.trajectory_rubrics(ctx(None), {"rubrics": RUBRICS})
    assert row.score is None and row.label == "skip"


async def test_neutral_skip_without_capability():
    caps = Capabilities.model_validate({"agent": "x", "provides": {"trajectory": False}})
    rows = await execute(
        BUILTIN_EVALUATORS["trajectory_rubrics"], ctx(["x"]), {"rubrics": RUBRICS}, caps
    )
    assert rows[0].score is None and rows[0].label == "skip"


def test_registered_requires():
    assert BUILTIN_EVALUATORS["trajectory_rubrics"].requires == frozenset(
        {"trajectory", "tool_calls"}
    )
