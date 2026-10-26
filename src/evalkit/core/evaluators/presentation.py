"""presentation_judge — rubric-scored judge over how the reply presents itself.

Grades form, not content: whether a unit label is shown, whether a table carries
the columns asked for, whether the reply relays an artifact instead of restating
it. Correctness of the figures belongs to answer_rubrics.

Score is points-weighted over binary per-rubric verdicts:

    score = sum(points of met rubrics) / sum(points of all rubrics)

Never fatal. There is no `clean` factor and no fabrication guard, because
presentation cannot fabricate — a missing unit label invents nothing. A `forbid`
rubric that is violated costs its points, so an assertion whose violation is a
product bug rather than a quality shortfall carries that weight through `points`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, Field

from evalkit.contracts.cases import PresentationJudge, PresentationRubric
from evalkit.core.evaluators.base import EvalContext, builtin_evaluator
from evalkit.core.evaluators.rubrics import missing_verdict_indices
from evalkit.core.judge import call_judge
from evalkit.store.base import ScoreRow

LOGGER = logging.getLogger(__name__)


class IncompleteRubricVerdicts(Exception):
    """The judge did not verdict every rubric exactly once."""


class PresentationVerdict(BaseModel):
    index: int = Field(
        description="The number this item was listed under in the prompt, counting from 1."
    )
    met: bool = Field(
        description="True when the answer has the required property. For an item"
        " marked MUST NOT, true means the answer avoided it."
    )
    evidence: str = Field(
        description="The span of the answer that decided this verdict, quoted. When"
        " the property is absent, say what was looked for and not found."
    )


class PresentationJudgeOutput(BaseModel):
    verdicts: list[PresentationVerdict]


SYSTEM_PROMPT = """\
You grade HOW an answer presents its information, not whether the information is \
correct. A figure may be wrong and still presented well; a figure may be right and \
presented badly. Judge only presentation.

You see the answer text and nothing else — not the tool results, not any generated \
file. If a property cannot be decided from the text, it is not met.

For each numbered item, return met/unmet and quote the span of the answer that \
decided it. For an item marked MUST NOT, `met` means the answer avoided it.

Return exactly one verdict per item, for every item."""


def _rubric_text(rubric: PresentationRubric) -> str:
    return f"MUST NOT: {rubric.forbid}" if rubric.forbid is not None else str(rubric.rubric)


def _render_rubric(index: int, rubric: PresentationRubric) -> str:
    line = f"{index}. {_rubric_text(rubric)}"
    if rubric.note:
        line += f"\n   note: {rubric.note}"
    return line


def build_user_prompt(answer: str, rubrics: list[PresentationRubric]) -> str:
    items = "\n".join(_render_rubric(i, rubric) for i, rubric in enumerate(rubrics, start=1))
    return f"ANSWER\n{answer}\n\nPRESENTATION ITEMS\n{items}"


def score_rubrics(rubrics: list[PresentationRubric], verdicts: list[PresentationVerdict]) -> float:
    """Points-weighted fraction of rubrics met. `rubrics` is non-empty by schema,
    so the denominator is always positive and the score is always a real number."""
    by_index = {v.index: v for v in verdicts}
    total = sum(r.points for r in rubrics)
    earned = sum(r.points for i, r in enumerate(rubrics, start=1) if by_index[i].met)
    return earned / total


async def _judge_verdicts(
    ctx: EvalContext, rubrics: list[PresentationRubric], base_prompt: str
) -> PresentationJudgeOutput:
    """One judge call, retried once when the verdict list has a gap.

    A gap is a judge lapse, not the agent's failure, so it is retried rather than
    scored — defaulting the missing rubrics to unmet would blame the agent.
    """
    current = base_prompt
    missing: list[int] = []
    for attempt in (1, 2):
        parsed, usage = await call_judge(
            ctx.judge_cfg,
            api_key=ctx.judge_api_key,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=current,
            output_type=PresentationJudgeOutput,
            session_id=ctx.session_id,
            evaluator_name="presentation_judge",
        )
        ctx.record_judge_usage(usage)
        missing = missing_verdict_indices(len(rubrics), [v.index for v in parsed.verdicts])
        if not missing:
            return parsed
        LOGGER.warning(
            "evals.presentation.incomplete_verdicts attempt=%s missing=%s", attempt, missing
        )
        current = (
            f"{base_prompt}\n\nYour previous response did not return exactly one "
            f"verdict for items {missing}. Return one verdict per item, for every "
            f"item from 1 to {len(rubrics)}."
        )
    raise IncompleteRubricVerdicts(f"judge left items {missing} unverdicted after a retry")


def _explanation(rubrics: list[PresentationRubric], verdicts: list[PresentationVerdict]) -> str:
    """Aggregates lead and `items` rides last, so a truncated explanation
    preview shows the counts. Items carry the rubric text — readers render
    explanations with no case config to join against — and the judge's quoted
    evidence as `note`."""
    by_index = {v.index: v for v in verdicts}
    items: list[dict[str, Any]] = []
    met_count = 0
    for i, r in enumerate(rubrics, start=1):
        v = by_index[i]
        met_count += v.met
        item: dict[str, Any] = {
            "index": i,
            "text": _rubric_text(r),
            "verdict": "satisfied" if v.met else "missing",
            "points_earned": r.points if v.met else 0,
            "points_possible": r.points,
        }
        if v.evidence:
            item["note"] = v.evidence
        items.append(item)
    return json.dumps({"met": met_count, "total": len(rubrics), "items": items}, ensure_ascii=False)


@builtin_evaluator("presentation_judge", uses_judge=True)
async def presentation_judge(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    cfg = PresentationJudge.model_validate(config)
    rubrics = cfg.rubrics
    parsed = await _judge_verdicts(ctx, rubrics, build_user_prompt(ctx.answer, rubrics))
    return [
        ScoreRow(
            name="presentation_judge",
            score=score_rubrics(rubrics, parsed.verdicts),
            explanation=_explanation(rubrics, parsed.verdicts),
            annotator_kind="LLM",
        )
    ]
