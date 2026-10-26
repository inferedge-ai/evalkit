"""trajectory_rubrics — checkpoint rubrics graded over the recorded trajectory.

One judge call over the full ATIF trajectory: every tool call, every tool
result and the agent's reasoning, with nothing truncated. Sized against 100
production sessions, a single eval run renders to ~45k chars at the median and
under 400k at the worst turn seen; compact ATIF JSON is 7-17% larger, so one
call fits.

    score = satisfied / total
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from evalkit.contracts.atif import Trajectory
from evalkit.contracts.cases import TrajectoryRubrics
from evalkit.core.evaluators.base import EvalContext, builtin_evaluator, neutral_skip
from evalkit.core.fixtures import compose_input
from evalkit.core.judge import call_judge
from evalkit.store.base import ScoreRow

LOGGER = logging.getLogger(__name__)

SCORE_NAME = "trajectory_rubric_score"


class TrajectoryVerdict(BaseModel):
    index: int = Field(
        description="The number this rubric was listed under in the prompt, counting from 1."
    )
    satisfied: bool = Field(
        description="True only when the trajectory or the final answer contain"
        " positive evidence for the rubric."
    )
    evidence: str = Field(
        default="",
        description='Where it shows: "main:4-5" for steps of the root trajectory,'
        ' "<trajectory_id>:7" for a subagent, or "final answer". Empty when not'
        " satisfied.",
    )
    note: str = Field(
        default="",
        description="One line on why this verdict, when not self-evident.",
    )


class TrajectoryJudgeOutput(BaseModel):
    verdicts: list[TrajectoryVerdict]


SYSTEM_PROMPT = """\
You are an impartial grader for a financial-research agent. You are given the
question the agent was asked, a numbered list of rubrics describing checkpoints
its work should show, its recorded trajectory, and its final answer. You return
one verdict per rubric. You are strict but fair: a rubric is satisfied only when
the trajectory or the final answer contain positive evidence for it. Silence is
not satisfaction; absence of contradiction is not satisfaction."""

_LEGEND = """\
The trajectory is an ATIF document. Steps run in step_id order. A tool call's
result is in that step's observation.results, matched by source_call_id; a
result with extra.is_error = true is a failed call. Delegated work is in
subagent_trajectories, each a trajectory of its own.

For each rubric return satisfied = true or false.

  true   the trajectory, or the final answer, shows the rubric was met. Cite
         where: "main:4-5" for steps 4-5 of the root trajectory,
         "<trajectory_id>:7" for step 7 of a subagent (use
         "subagent_trajectories[0]:7" when it has no trajectory_id), or
         "final answer".
  false  no positive evidence anywhere in the trajectory or the final answer.

Match semantically: an action satisfies a rubric regardless of what the tool or
mechanism behind it is called. A figure satisfies a rubric when it equals the
rubric's figure after unit conversion, accepting normal rounding unless the
rubric states its own band. Grade every rubric independently of the others."""


def trajectory_json(trajectory: Trajectory) -> str:
    return json.dumps(
        trajectory.model_dump(exclude_none=True), ensure_ascii=False, separators=(",", ":")
    )


def user_prompt(question: str, rubrics: list[str], trajectory: str, answer: str) -> str:
    numbered = "\n".join(f"{i}. {r}" for i, r in enumerate(rubrics, 1))
    return "\n".join(
        [
            _LEGEND,
            "",
            "QUESTION",
            question,
            "",
            "RUBRICS",
            numbered,
            "",
            "TRAJECTORY (ATIF JSON)",
            trajectory,
            "",
            "FINAL ANSWER",
            answer,
        ]
    )


class IncompleteVerdicts(Exception):
    """The judge did not verdict every rubric exactly once, even after a retry."""


def _missing(total: int, parsed: TrajectoryJudgeOutput) -> list[int]:
    seen: dict[int, int] = {}
    for v in parsed.verdicts:
        seen[v.index] = seen.get(v.index, 0) + 1
    return [i for i in range(1, total + 1) if seen.get(i, 0) != 1]


async def _judge(ctx: EvalContext, base_prompt: str, total: int) -> TrajectoryJudgeOutput:
    current = base_prompt
    missing: list[int] = []
    for attempt in (1, 2):
        parsed, usage = await call_judge(
            ctx.judge_cfg,
            api_key=ctx.judge_api_key,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=current,
            output_type=TrajectoryJudgeOutput,
            session_id=ctx.session_id,
            evaluator_name="trajectory_rubrics",
        )
        ctx.record_judge_usage(usage)
        missing = _missing(total, parsed)
        if not missing:
            return parsed
        LOGGER.warning(
            "evals.trajectory_rubrics.incomplete_verdicts attempt=%s missing=%s", attempt, missing
        )
        current = (
            f"{base_prompt}\n\nYour previous response did not return exactly one verdict for "
            f"rubrics {missing}. Return one verdict per rubric, for every rubric from 1 to {total}."
        )
    raise IncompleteVerdicts(f"judge left rubrics {missing} unverdicted after a retry")


def _explanation(rubrics: list[str], verdicts: list[TrajectoryVerdict]) -> str:
    by_index = {v.index: v for v in verdicts}
    rows = []
    for index, rubric in enumerate(rubrics, 1):
        v = by_index[index]
        rows.append(
            {
                "index": index,
                "rubric": rubric,
                "verdict": "satisfied" if v.satisfied else "unsatisfied",
                "evidence": v.evidence if v.satisfied else "",
                "note": v.note,
            }
        )
    return json.dumps(
        {
            "satisfied": sum(1 for v in verdicts if v.satisfied),
            "total": len(rubrics),
            "verdicts": rows,
        },
        ensure_ascii=False,
    )


@builtin_evaluator(
    "trajectory_rubrics",
    requires={"trajectory", "tool_calls"},
    emits=(SCORE_NAME,),
    uses_judge=True,
)
async def trajectory_rubrics(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    cfg = TrajectoryRubrics.model_validate(config)
    if ctx.trajectory_view is None:
        return [neutral_skip(SCORE_NAME, "no trajectory recorded for this run")]

    trajectory = trajectory_json(ctx.trajectory_view.trajectory)
    question = str(compose_input(ctx.case.input, ctx.case.files).get("question", ""))
    parsed = await _judge(
        ctx, user_prompt(question, cfg.rubrics, trajectory, ctx.answer), len(cfg.rubrics)
    )
    score = sum(1 for v in parsed.verdicts if v.satisfied) / len(cfg.rubrics)
    return [
        ScoreRow(
            name=SCORE_NAME,
            score=score,
            explanation=_explanation(cfg.rubrics, parsed.verdicts),
            annotator_kind="LLM",
        )
    ]
