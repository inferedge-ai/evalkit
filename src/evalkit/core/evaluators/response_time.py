from __future__ import annotations

from typing import Any

from evalkit.contracts.cases import MaxResponseTime
from evalkit.core.evaluators.base import EvalContext, builtin_evaluator
from evalkit.store.base import ScoreRow


@builtin_evaluator("max_response_time")
async def max_response_time(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    cfg = MaxResponseTime.model_validate(config)
    seconds = ctx.wall_ms / 1000
    passed = seconds <= cfg.max_seconds
    return [
        ScoreRow(
            name="max_response_time",
            score=1.0 if passed else 0.0,
            explanation=f"run took {seconds:.1f}s against a cap of {cfg.max_seconds:g}s",
        )
    ]
