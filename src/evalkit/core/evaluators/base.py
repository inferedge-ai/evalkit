from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from evalkit.contracts.harness import Capabilities
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.judge import JudgeUsage
from evalkit.core.trajectory import RunMetrics, TrajectoryView
from evalkit.store.base import CaseView, ScoreRow

PASS_THRESHOLD = 0.8
WARN_THRESHOLD = 0.5


def label_from_score(score: float) -> str:
    if score >= PASS_THRESHOLD:
        return "pass"
    if score >= WARN_THRESHOLD:
        return "warn"
    return "fail"


# score=None excludes skipped evaluators from aggregates — returning 1.0 would
# inflate the overall score.
def neutral_skip(name: str, reason: str = "capability not provided") -> ScoreRow:
    return ScoreRow(name=name, score=None, label="skip", explanation=reason)


@dataclass
class EvalContext:
    case: CaseView
    answer: str
    trajectory_view: TrajectoryView | None
    wall_ms: int
    judge_cfg: JudgeSettings
    metrics: RunMetrics | None = None
    session_id: str | None = None
    cost_map: dict[str, Any] = field(default_factory=dict)
    judge_usage: JudgeUsage = field(default_factory=JudgeUsage)
    output: dict[str, Any] | None = None
    judge_api_key: str | None = None

    def record_judge_usage(self, usage: JudgeUsage) -> None:
        self.judge_usage.input_tokens += usage.input_tokens
        self.judge_usage.output_tokens += usage.output_tokens


EvalFn = Callable[[EvalContext, dict[str, Any]], Awaitable[list[ScoreRow]]]


@dataclass(frozen=True)
class BuiltinEval:
    name: str
    fn: EvalFn
    requires: frozenset[str]
    kind: Literal["evaluator", "gauge"]
    emits: tuple[str, ...]
    uses_judge: bool = False


BUILTIN_EVALUATORS: dict[str, BuiltinEval] = {}
BUILTIN_GAUGES: dict[str, BuiltinEval] = {}


def builtin_evaluator(
    name: str,
    requires: set[str] | None = None,
    emits: tuple[str, ...] | None = None,
    uses_judge: bool = False,
):
    def decorate(fn: EvalFn) -> EvalFn:
        BUILTIN_EVALUATORS[name] = BuiltinEval(
            name=name,
            fn=fn,
            requires=frozenset(requires or set()),
            kind="evaluator",
            emits=emits or (name,),
            uses_judge=uses_judge,
        )
        return fn

    return decorate


def builtin_gauge(name: str, requires: set[str] | None = None):
    def decorate(fn: EvalFn) -> EvalFn:
        BUILTIN_GAUGES[name] = BuiltinEval(
            name=name, fn=fn, requires=frozenset(requires or set()), kind="gauge", emits=(name,)
        )
        return fn

    return decorate


def gate(evaluator: BuiltinEval, capabilities: Capabilities) -> bool:
    return all(getattr(capabilities.provides, flag, False) for flag in evaluator.requires)


async def execute(
    evaluator: BuiltinEval,
    ctx: EvalContext,
    config: dict[str, Any],
    capabilities: Capabilities,
) -> list[ScoreRow]:
    if not gate(evaluator, capabilities):
        missing = sorted(
            flag for flag in evaluator.requires if not getattr(capabilities.provides, flag, False)
        )
        row = neutral_skip(evaluator.name, f"harness does not provide: {', '.join(missing)}")
        if evaluator.kind == "gauge":
            row.kind = "gauge"
            row.label = "info"
        return [row]
    rows = await evaluator.fn(ctx, config)
    for row in rows:
        if evaluator.kind == "gauge":
            row.kind = "gauge"
            row.label = row.label or "info"
        elif row.label is None and row.score is not None:
            row.label = label_from_score(row.score)
    return rows
