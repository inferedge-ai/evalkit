from __future__ import annotations

from typing import Any

from evalkit.core.costmap import cost_usd, resolve_rates
from evalkit.core.evaluators.base import EvalContext, builtin_gauge
from evalkit.store.base import ScoreRow


def _reading(name: str, value: float | None, explanation: str | None = None) -> list[ScoreRow]:
    if value is None:
        return [
            ScoreRow(name=name, kind="gauge", score=None, label="info", explanation="no reading")
        ]
    return [ScoreRow(name=name, kind="gauge", score=value, label="info", explanation=explanation)]


@builtin_gauge("gauge_duration_seconds")
async def gauge_duration_seconds(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    return _reading("gauge_duration_seconds", round(ctx.wall_ms / 1000, 1))


@builtin_gauge("gauge_input_tokens", requires={"token_usage"})
async def gauge_input_tokens(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    value = ctx.metrics.input_tokens if ctx.metrics else None
    return _reading("gauge_input_tokens", value)


@builtin_gauge("gauge_output_tokens", requires={"token_usage"})
async def gauge_output_tokens(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    value = ctx.metrics.output_tokens if ctx.metrics else None
    return _reading("gauge_output_tokens", value)


@builtin_gauge("gauge_cached_tokens", requires={"token_usage"})
async def gauge_cached_tokens(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    value = ctx.metrics.cached_tokens if ctx.metrics else None
    return _reading("gauge_cached_tokens", value)


@builtin_gauge("gauge_agent_cost_usd", requires={"token_usage"})
async def gauge_agent_cost_usd(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    value = ctx.metrics.cost_usd if ctx.metrics else None
    return _reading("gauge_agent_cost_usd", round(value, 4) if value is not None else None)


@builtin_gauge("gauge_judge_cost_usd")
async def gauge_judge_cost_usd(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    usage = ctx.judge_usage
    if usage.input_tokens == 0 and usage.output_tokens == 0:
        return _reading("gauge_judge_cost_usd", None)
    rates = resolve_rates(ctx.cost_map, ctx.judge_cfg.model)
    if rates is None:
        return _reading("gauge_judge_cost_usd", None)
    total = cost_usd(rates, usage.input_tokens, usage.output_tokens, 0)
    return _reading(
        "gauge_judge_cost_usd",
        round(total, 4),
        f"{usage.input_tokens} in / {usage.output_tokens} out judge tokens",
    )


@builtin_gauge("gauge_total_tool_calls", requires={"trajectory", "tool_calls"})
async def gauge_total_tool_calls(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    value = len(ctx.trajectory_view.tool_calls()) if ctx.trajectory_view else None
    return _reading("gauge_total_tool_calls", value)


@builtin_gauge("gauge_errored_tool_calls", requires={"trajectory", "tool_calls"})
async def gauge_errored_tool_calls(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    value = ctx.trajectory_view.errored_tool_call_count() if ctx.trajectory_view else None
    return _reading("gauge_errored_tool_calls", value)


@builtin_gauge("gauge_subagents", requires={"trajectory"})
async def gauge_subagents(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    value = ctx.trajectory_view.subagent_count() if ctx.trajectory_view else None
    return _reading("gauge_subagents", value)


@builtin_gauge("gauge_turns", requires={"trajectory"})
async def gauge_turns(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    value = ctx.trajectory_view.turn_count() if ctx.trajectory_view else None
    return _reading("gauge_turns", value)
