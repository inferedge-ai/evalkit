from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from evalkit.contracts.custom_evaluator import EvalCase, EvalRun, ScoreItem
from evalkit.core.custom_eval import EvalError

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class PythonEvaluator:
    name: str
    kind: Literal["evaluator", "gauge"]
    fn: Callable[[EvalCase, EvalRun], Any]
    requires_trajectory: bool = False

    def __call__(self, case: EvalCase, run: EvalRun) -> Any:
        return self.fn(case, run)


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
) -> list[ScoreItem] | EvalError:
    run = EvalRun(answer=answer, output=output, trajectory=trajectory)
    try:
        if inspect.iscoroutinefunction(evaluator.fn):
            result = await evaluator.fn(case, run)
        else:
            result = await asyncio.to_thread(evaluator.fn, case, run)
            if inspect.isawaitable(result):
                result = await result
        return _normalise(evaluator.name, result)
    except Exception as exc:
        LOGGER.warning("python evaluator %s errored: %s", evaluator.name, exc)
        return EvalError(evaluator=evaluator.name, message=f"{type(exc).__name__}: {exc}")
