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
    """Turn a function into a Python evaluator for `run_experiment`.

    Use it bare (`@evaluator`) or with options (`@evaluator(name=...)`). The function
    receives the same `EvalCase` and `EvalRun` the Custom Evaluator API sends: the
    case id, input, expected block and this evaluator's config from the case YAML
    (`case.config`); the answer, structured output and ATIF trajectory as a dict.
    It returns a float, a `ScoreItem`, or a list of them.

    Args:
        fn: The function, when used bare.
        name: The name cases use under `evaluators.custom`. Defaults to the
            function's name.
        kind: `evaluator` for a score, `gauge` for a measurement.
        requires_trajectory: Skip runs that have no trajectory.

    Returns:
        The evaluator, or a decorator that makes one.
    """

    def wrap(function: Callable[..., Any]) -> PythonEvaluator:
        return PythonEvaluator(
            name=name or function.__name__,
            kind=kind,
            fn=function,
            requires_trajectory=requires_trajectory,
        )

    return wrap(fn) if fn is not None else wrap
