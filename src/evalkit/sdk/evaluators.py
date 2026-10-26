from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal, overload

from evalkit.core.python_eval import PythonEvaluator

Kind = Literal["evaluator", "gauge"]


@overload
def evaluator(fn: Callable[..., Any], /) -> PythonEvaluator: ...


@overload
def evaluator(
    *, name: str | None = None, kind: Kind = "evaluator", requires_trajectory: bool = False
) -> Callable[[Callable[..., Any]], PythonEvaluator]: ...


def evaluator(
    fn: Callable[..., Any] | None = None,
    /,
    *,
    name: str | None = None,
    kind: Kind = "evaluator",
    requires_trajectory: bool = False,
) -> PythonEvaluator | Callable[[Callable[..., Any]], PythonEvaluator]:
    def wrap(function: Callable[..., Any]) -> PythonEvaluator:
        return PythonEvaluator(
            name=name or function.__name__,
            kind=kind,
            fn=function,
            requires_trajectory=requires_trajectory,
        )

    return wrap(fn) if fn is not None else wrap
