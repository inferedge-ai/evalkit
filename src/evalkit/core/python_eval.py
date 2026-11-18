from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from evalkit.contracts.custom_evaluator import EvalCase, EvalRun, ScoreItem
from evalkit.core.custom_eval import EvalError
from evalkit.core.errors import error_text
from evalkit.core.evaluators.base import EvalContext
from evalkit.core.judge import call_judge

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PythonEvaluator:
    name: str
    kind: Literal["evaluator", "gauge"]
    fn: Callable[..., Any]
    requires_trajectory: bool = False
    uses_judge: bool = False

    def __call__(self, *args: Any) -> Any:
        return self.fn(*args)


@dataclass(frozen=True)
class EvaluatorJudge:
    ctx: EvalContext
    evaluator_name: str

    async def ask[T: BaseModel](
        self, *, system_prompt: str, user_prompt: str, output_type: type[T]
    ) -> T:
        answer, usage = await call_judge(
            self.ctx.require_judge(),
            api_key=self.ctx.judge_api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            output_type=output_type,
            session_id=self.ctx.session_id,
            evaluator_name=self.evaluator_name,
        )
        self.ctx.record_judge_usage(usage)
        return answer


def _normalise(name: str, result: Any) -> list[ScoreItem]:
    if isinstance(result, ScoreItem):
        return [result]
    if isinstance(result, int | float) and not isinstance(result, bool):
        return [ScoreItem(name=name, score=float(result))]
    if isinstance(result, list) and all(isinstance(item, ScoreItem) for item in result):
        return result
    raise TypeError(
        f"evaluator {name} returned {type(result).__name__}; "
        "expected float, ScoreItem or list[ScoreItem]"
    )


async def score_with_python_evaluator(
    evaluator: PythonEvaluator,
    *,
    case: EvalCase,
    answer: str,
    output: dict | None,
    trajectory: dict,
    judge: EvaluatorJudge | None = None,
) -> list[ScoreItem] | EvalError:
    run = EvalRun(answer=answer, output=output, trajectory=trajectory)
    args = (case, run) if judge is None else (case, run, judge)
    try:
        if inspect.iscoroutinefunction(evaluator.fn):
            result = await evaluator.fn(*args)
        else:
            result = await asyncio.to_thread(evaluator.fn, *args)
            if inspect.isawaitable(result):
                result = await result
        return _normalise(evaluator.name, result)
    except Exception as exc:
        error = error_text(exc)
        LOGGER.warning("python evaluator %s errored: %s", evaluator.name, error)
        return EvalError(evaluator=evaluator.name, message=error)
