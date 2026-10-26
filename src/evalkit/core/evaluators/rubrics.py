"""The answer-rubric evaluator — one judge call per case, seven named scores.

The metrics section carries the formula for each. Nothing here is a gauge: the
content score, its two factors, the three per-type rates and the citation rate
are all evaluations, so each lands in the evaluator matrix and can be pooled
across the corpus.

    coverage  = attempted / total       did the answer address the rubric at all
    accuracy  = satisfied / attempted   of what it addressed, how much is right
    recall    = satisfied / total       identically coverage x accuracy

`accuracy` is not precision: its denominator does not count what the answer
volunteered that no rubric asked about, so a long answer is not marked down for
its length. Nothing here measures unrequested content at all.

`source` is graded on its own axis and never enters the content counts — with
every rubric counting once, a `source` would otherwise weigh as much as the fact
it hangs off, and a case where every fact carries one would put half its content
score on citations.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from evalkit.contracts.cases import AnswerRubrics, AnswerRubricType, Rubric
from evalkit.core.evaluators.base import EvalContext, builtin_evaluator, label_from_score
from evalkit.core.fixtures import compose_input
from evalkit.core.judge import call_judge
from evalkit.store.base import ScoreRow

LOGGER = logging.getLogger(__name__)

RUBRIC_TYPES: tuple[AnswerRubricType, ...] = (
    "factual_retrieval",
    "derived_computation",
    "analysis_interpretation",
)

SCORE_NAME = "answer_rubric_score"
COVERAGE_NAME = "answer_rubric_coverage"
ACCURACY_NAME = "answer_rubric_accuracy"
CITATION_NAME = "answer_rubric_citation"


def type_score_name(rubric_type: AnswerRubricType) -> str:
    return f"answer_rubric_{rubric_type}"


ANSWER_RUBRIC_EMITS: tuple[str, ...] = (
    SCORE_NAME,
    COVERAGE_NAME,
    ACCURACY_NAME,
    *(type_score_name(t) for t in RUBRIC_TYPES),
    CITATION_NAME,
)

RubricVerdictLabel = Literal["satisfied", "incorrect", "absent"]

VERDICTS: tuple[RubricVerdictLabel, ...] = ("satisfied", "incorrect", "absent")


class SourceVerdict(BaseModel):
    verdict: RubricVerdictLabel = Field(
        description="""\
satisfied: the answer attributes this fact to that source.
incorrect: it attributes the fact to a DIFFERENT source.
absent: it states the fact with no attribution at all."""
    )
    stated: str = Field(
        default="",
        description="The attribution the answer gave, quoted verbatim. Empty when absent.",
    )
    note: str = Field(
        default="",
        description="One line on why this verdict, when not self-evident.",
    )


class RubricVerdict(BaseModel):
    index: int = Field(
        description="The number this rubric was listed under in the prompt, counting from 1."
    )
    verdict: RubricVerdictLabel = Field(
        description="""\
satisfied: the answer meets the rubric.
incorrect: it attaches a value or assertion to the rubric's subject and that
           does not meet the rubric.
absent: it attaches no value or assertion to the rubric's subject."""
    )
    stated: str = Field(
        default="",
        description="What the answer said on this point, quoted verbatim. Empty when absent.",
    )
    note: str = Field(
        default="",
        description="One line on why this verdict. Required for incorrect and absent:"
        " what the answer said or left out, and how that misses the rubric.",
    )
    # No default, so `source` stays in the schema's `required` list. Optional it
    # renders as `anyOf: [$ref, null]` outside `required`, and Gemini never emits
    # the field at all.
    source: SourceVerdict | None = Field(
        description="A verdict object for a rubric shown with a `source:` line;"
        " null for a rubric shown without one.",
    )


class RubricJudgeOutput(BaseModel):
    verdicts: list[RubricVerdict]


@dataclass(frozen=True)
class GradedRubric:
    """One rubric with the verdict it received.

    `source_verdict` is None where the case declared no `source` — the
    judge-gap check guarantees that a rubric shown with one comes back with a
    sub-verdict, so None here means "no source asked for", never "not graded".
    """

    type: AnswerRubricType
    verdict: RubricVerdictLabel
    source_verdict: RubricVerdictLabel | None = None


class RubricMetrics(BaseModel):
    coverage: float | None = None
    accuracy: float | None = None
    recall: float | None = None

    satisfied: int = 0
    incorrect: int = 0
    absent: int = 0
    total: int = 0
    attempted: int = 0


class CitationMetrics(BaseModel):
    coverage: float | None = None
    accuracy: float | None = None
    rate: float | None = None

    satisfied: int = 0
    incorrect: int = 0
    absent: int = 0
    eligible: int = 0
    attempted: int = 0


def _counts(verdicts: list[RubricVerdictLabel]) -> dict[str, int]:
    counted: dict[str, int] = {verdict: 0 for verdict in VERDICTS}
    for verdict in verdicts:
        if verdict not in counted:
            raise ValueError(f"unknown verdict {verdict!r}")
        counted[verdict] += 1
    return counted


def aggregate(rubrics: list[GradedRubric]) -> RubricMetrics:
    n = _counts([r.verdict for r in rubrics])
    total = sum(n.values())
    attempted = n["satisfied"] + n["incorrect"]
    return RubricMetrics(
        # None, never 0.0: bucket aggregation drops None but averages 0.0 in, so
        # an unmeasured metric would otherwise drag down every aggregate it
        # touches.
        coverage=attempted / total if total else None,
        accuracy=n["satisfied"] / attempted if attempted else None,
        recall=n["satisfied"] / total if total else None,
        satisfied=n["satisfied"],
        incorrect=n["incorrect"],
        absent=n["absent"],
        total=total,
        attempted=attempted,
    )


def by_type(rubrics: list[GradedRubric]) -> dict[AnswerRubricType, RubricMetrics]:
    """The same count over each type's subset.

    A type the case carries no rubrics of yields no entry, so its evaluator
    skips rather than scoring zero.
    """
    present = {r.type for r in rubrics}
    return {t: aggregate([r for r in rubrics if r.type == t]) for t in present}


def citation_rate(rubrics: list[GradedRubric]) -> CitationMetrics:
    """The citation axis, over the sources the answer had a chance to give.

    A `source` counts only where the answer actually stated the parent fact. An
    answer that never mentioned the fact had no chance to cite it, and counting
    that as a citation miss would make this rate a shadow of the content rate
    instead of saying something new.
    """
    eligible = [
        r.source_verdict for r in rubrics if r.source_verdict is not None and r.verdict != "absent"
    ]
    n = _counts(eligible)
    count = sum(n.values())
    attempted = n["satisfied"] + n["incorrect"]
    return CitationMetrics(
        coverage=attempted / count if count else None,
        accuracy=n["satisfied"] / attempted if attempted else None,
        rate=n["satisfied"] / count if count else None,
        satisfied=n["satisfied"],
        incorrect=n["incorrect"],
        absent=n["absent"],
        eligible=count,
        attempted=attempted,
    )


def missing_verdict_indices(expected_count: int, indices: list[int]) -> list[int]:
    """Rubric indices (1-based) the judge did not verdict exactly once."""
    seen: dict[int, int] = {}
    for index in indices:
        seen[index] = seen.get(index, 0) + 1
    return [i for i in range(1, expected_count + 1) if seen.get(i, 0) != 1]


def missing_source_indices(
    sourced_indices: list[int], graded_source_indices: set[int]
) -> list[int]:
    """Indices shown with a `source:` line that came back with no source verdict."""
    return [i for i in sourced_indices if i not in graded_source_indices]


PREAMBLE = """\
You are an experienced financial analyst grading another analyst's work. You are
given the question that was asked, the answer, and a numbered list of rubrics,
and you return a verdict for each. You are strict but fair: a rubric is
satisfied only on positive evidence."""

_VERDICT_LEGEND = """\
Each line below is one rubric to verdict, formatted `<n>. <assertion>`.

For each rubric return one verdict:
  satisfied  the answer meets the rubric
  incorrect  the answer attaches a value or assertion to the rubric's subject,
             and it does not meet the rubric
  absent     the answer attaches no value or assertion to the rubric's subject

A rubric shown with a `source:` line also gets a verdict on that line: whether
the answer attributes this fact to that source. Equivalent naming counts — "the
Q3 FY2024 results", "the Q3 press release" and "the company's fiscal Q3 2024
results" are the same source. Attributing the fact to a DIFFERENT document is
`incorrect`; stating the fact with no attribution at all is `absent`. The
attribution may sit anywhere in the answer — beside the fact, in the paragraph
introducing it, or in a references section at the end — as long as it is clear
that it covers this fact.

Grade each `source:` line independently of the rubric above it, and grade every
rubric independently of the others. Silence is not satisfaction; absence of
contradiction is not satisfaction.

Every `incorrect` or `absent` verdict carries a note saying what the answer said
or left out and how that misses the rubric."""

_MATCHING_LEGEND = """\
NUMERIC MATCHING
A figure in the answer satisfies a rubric when it matches the rubric's figure
after unit conversion, accepting normal rounding: an answer figure that is a
rounding of the rubric's figure satisfies it. Against "3,172.6 million",
"3,173 million" and "3.17 billion" are satisfied; "3,180 million" is a
different figure, not a rounding, so it is incorrect.

Where the rubric states its own acceptance criterion ("±1 million", "any
figure between X and Y", "either the standalone or the consolidated figure"),
that criterion replaces this default — accept exactly what it accepts.

Equality is on the numeric value, not the string: "$3,172.6 million",
"3172.60 million" and "$3.1726 billion" are the same figure."""

_UNIT_LEGEND = """\
UNITS
Each rubric states its unit. Convert the answer's figure into that unit before
comparing; never compare raw figures.
  scale:  lakh = 1e5 · crore = 1e7 · million = 1e6 · billion = 1e9
  rate:   1 percent = 100 bps
  sign:   a figure in parentheses inside a financial statement, table or
          calculation is negative — "(1,234)" is -1234. Parentheses in prose are
          not a sign: "revenue rose 12% (driven by growth)" carries no negative.
          Direction words — outflow, loss, decline, decrease — also mean negative."""


def render_rubric(index: int, rubric: Rubric) -> str:
    line = f"{index:>2}. {rubric.rubric}"
    if rubric.source is not None:
        line += f"\n    source: {rubric.source}"
    return line


def user_prompt(question: str, rubrics: list[Rubric], answer: str) -> str:
    lines = [render_rubric(i, rubric) for i, rubric in enumerate(rubrics, start=1)]
    return "\n".join(
        [
            _VERDICT_LEGEND,
            "",
            _MATCHING_LEGEND,
            "",
            _UNIT_LEGEND,
            "",
            "QUESTION",
            question,
            "",
            "RUBRICS",
            "\n\n".join(lines),
            "",
            "ANSWER",
            answer,
        ]
    )


class IncompleteVerdicts(Exception):
    """The judge did not verdict every rubric, and every source, exactly once,
    with a note on every verdict that is not satisfied."""


def unexplained_unsatisfied_indices(verdicts: list[RubricVerdict]) -> list[int]:
    return [v.index for v in verdicts if v.verdict != "satisfied" and not v.note.strip()]


def _gaps(rubrics: list[Rubric], parsed: RubricJudgeOutput) -> str | None:
    """What the judge left ungraded, as a phrase for the retry, or None."""
    missing = missing_verdict_indices(len(rubrics), [v.index for v in parsed.verdicts])
    sourced = [i for i, r in enumerate(rubrics, start=1) if r.source is not None]
    graded_sources = {v.index for v in parsed.verdicts if v.source is not None}
    missing_sources = missing_source_indices(sourced, graded_sources)
    unexplained = unexplained_unsatisfied_indices(parsed.verdicts)
    parts = []
    if missing:
        parts.append(f"exactly one verdict for rubrics {missing}")
    if missing_sources:
        parts.append(f"a source verdict for rubrics {missing_sources}")
    if unexplained:
        parts.append(f"a note on non-satisfied rubrics {unexplained}")
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + f" and {parts[-1]}"


async def _judge_verdicts(
    ctx: EvalContext, rubrics: list[Rubric], base_prompt: str
) -> RubricJudgeOutput:
    """One judge call, retried once when the verdict list has a gap.

    A gap is not scored as the agent's failure — an incomplete response is a
    judge lapse, and defaulting the missing rubrics to `absent` would blame the
    agent for it.
    """
    current = base_prompt
    gaps: str | None = None
    for attempt in (1, 2):
        parsed, usage = await call_judge(
            ctx.judge_cfg,
            api_key=ctx.judge_api_key,
            system_prompt=PREAMBLE,
            user_prompt=current,
            output_type=RubricJudgeOutput,
            session_id=ctx.session_id,
            evaluator_name="answer_rubrics",
        )
        ctx.record_judge_usage(usage)
        gaps = _gaps(rubrics, parsed)
        if gaps is None:
            return parsed
        LOGGER.warning("evals.answer_rubrics.incomplete_verdicts attempt=%s gaps=%s", attempt, gaps)
        current = (
            f"{base_prompt}\n\nYour previous response did not return {gaps}. Return "
            f"one verdict per rubric, for every rubric from 1 to {len(rubrics)}, a "
            "source verdict for every rubric shown with a `source:` line, and a note on "
            "every verdict that is not satisfied."
        )
    raise IncompleteVerdicts(f"judge left {gaps} after a retry")


def _grade(rubrics: list[Rubric], verdicts: list[RubricVerdict]) -> list[GradedRubric]:
    by_index = {v.index: v for v in verdicts}
    graded = []
    for index, rubric in enumerate(rubrics, start=1):
        verdict = by_index[index]
        graded.append(
            GradedRubric(
                type=rubric.type,
                verdict=verdict.verdict,
                source_verdict=(
                    verdict.source.verdict
                    if rubric.source is not None and verdict.source is not None
                    else None
                ),
            )
        )
    return graded


def _explanation(
    content: RubricMetrics,
    per_type: dict[AnswerRubricType, RubricMetrics],
    citation: CitationMetrics,
    rubrics: list[Rubric],
    verdicts: list[RubricVerdict],
) -> str:
    """The only structured field a score row carries, so the counts and
    verdicts ride in it as JSON. The scorecard's pooled rates and its BY TYPE /
    CITATION tables are all read back out of here. Each verdict carries its
    rubric's text and type so the table is readable without the case config."""
    by_index = {index: rubric for index, rubric in enumerate(rubrics, start=1)}
    rows = []
    for verdict in verdicts:
        rubric = by_index.get(verdict.index)
        rows.append(
            {
                **verdict.model_dump(),
                "rubric": rubric.rubric if rubric else None,
                "type": rubric.type if rubric else None,
            }
        )
    return json.dumps(
        {
            **content.model_dump(),
            "by_type": {t: m.model_dump() for t, m in per_type.items()},
            "citation": citation.model_dump(),
            "verdicts": rows,
        },
        ensure_ascii=False,
    )


def _row(name: str, score: float | None, explanation: str, skip_reason: str) -> ScoreRow:
    if score is None:
        return ScoreRow(name=name, score=None, label="skip", explanation=skip_reason)
    return ScoreRow(
        name=name,
        score=score,
        label=label_from_score(score),
        explanation=explanation,
        annotator_kind="LLM",
    )


@builtin_evaluator("answer_rubrics", emits=ANSWER_RUBRIC_EMITS, uses_judge=True)
async def answer_rubrics(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    cfg = AnswerRubrics.model_validate(config)
    question = str(compose_input(ctx.case.input, ctx.case.files).get("question", ""))
    parsed = await _judge_verdicts(ctx, cfg.rubrics, user_prompt(question, cfg.rubrics, ctx.answer))

    graded = _grade(cfg.rubrics, parsed.verdicts)
    content = aggregate(graded)
    per_type = by_type(graded)
    citation = citation_rate(graded)

    # Every scored name carries the SAME explanation JSON. The scorecard pools
    # each name from its own row, so each one has to be able to find its counts
    # there; and a reader opening any cell in the matrix gets the whole verdict
    # list rather than only the slice that name scored.
    explanation = _explanation(content, per_type, citation, cfg.rubrics, parsed.verdicts)

    rows = [
        _row(SCORE_NAME, content.recall, explanation, "no rubrics — nothing to score"),
        _row(COVERAGE_NAME, content.coverage, explanation, "no rubrics — nothing to engage"),
        _row(
            ACCURACY_NAME,
            content.accuracy,
            explanation,
            "no rubric was engaged — accuracy undefined",
        ),
        _row(
            CITATION_NAME,
            citation.rate,
            explanation,
            "no source was eligible — the answer stated none of the sourced facts",
        ),
    ]
    for rubric_type in RUBRIC_TYPES:
        metrics = per_type.get(rubric_type)
        rows.append(
            _row(
                type_score_name(rubric_type),
                metrics.recall if metrics else None,
                explanation,
                f"the case carries no {rubric_type} rubrics",
            )
        )
    return rows
