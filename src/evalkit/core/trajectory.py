import json

from pydantic import BaseModel, Field

from evalkit.contracts.atif import ContentPart, ObservationResult, Step, ToolCall, Trajectory
from evalkit.core.costmap import cost_usd, resolve_rates


def _message_text(message: str | list[ContentPart] | None) -> str:
    if message is None:
        return ""
    if isinstance(message, str):
        return message
    return " ".join(part.text for part in message if part.type == "text" and part.text)


def _result_text(result: ObservationResult) -> str:
    if result.content is None:
        return ""
    if isinstance(result.content, str):
        return result.content
    return " ".join(part.text for part in result.content if part.type == "text" and part.text)


class TokenTotals(BaseModel):
    input: int | None = None
    output: int | None = None
    cached: int | None = None


class TrajectoryMetrics(BaseModel):
    agent: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    total_tool_calls: int = 0
    errored_tool_calls: int = 0
    turns: int = 0


class RunMetrics(BaseModel):
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    total_tool_calls: int = 0
    errored_tool_calls: int = 0
    subagents: int = 0
    turns: int = 0
    by_trajectory: dict[str, TrajectoryMetrics] = Field(default_factory=dict)


def _add_reported[Num: (int, float)](total: Num | None, value: Num | None) -> Num | None:
    if total is None:
        return value
    if value is None:
        return total
    return total + value


def _errored_results(step: Step) -> int:
    if step.observation is None:
        return 0
    return sum(1 for r in step.observation.results if (r.extra or {}).get("is_error") is True)


def _own_metrics(trajectory: Trajectory, agent: str) -> TrajectoryMetrics:
    metrics = TrajectoryMetrics(agent=agent)
    for step in trajectory.steps:
        if step.source == "agent":
            metrics.turns += 1
        metrics.total_tool_calls += len(step.tool_calls or [])
        metrics.errored_tool_calls += _errored_results(step)
        if step.metrics is not None:
            metrics.input_tokens = _add_reported(metrics.input_tokens, step.metrics.prompt_tokens)
            metrics.output_tokens = _add_reported(
                metrics.output_tokens, step.metrics.completion_tokens
            )
            metrics.cached_tokens = _add_reported(metrics.cached_tokens, step.metrics.cached_tokens)
            metrics.cost_usd = _add_reported(metrics.cost_usd, step.metrics.cost_usd)
    if metrics.cost_usd is not None:
        metrics.cost_usd = round(metrics.cost_usd, 10)
    return metrics


class TrajectoryView:
    def __init__(self, trajectory: Trajectory):
        self.trajectory = trajectory

    def _all_steps(self) -> list[Step]:
        steps = list(self.trajectory.steps)
        for sub in self.trajectory.subagent_trajectories or []:
            steps.extend(sub.steps)
        return steps

    def tool_calls(self) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for step in self._all_steps():
            calls.extend(step.tool_calls or [])
        return calls

    def errored_tool_call_count(self) -> int:
        return sum(_errored_results(step) for step in self._all_steps())

    def token_totals(self) -> TokenTotals:
        final = self.trajectory.final_metrics
        if final is not None and any(
            v is not None
            for v in (
                final.total_prompt_tokens,
                final.total_completion_tokens,
                final.total_cached_tokens,
            )
        ):
            return TokenTotals(
                input=final.total_prompt_tokens,
                output=final.total_completion_tokens,
                cached=final.total_cached_tokens,
            )
        totals = {"input": None, "output": None, "cached": None}
        keys = {"input": "prompt_tokens", "output": "completion_tokens", "cached": "cached_tokens"}
        for step in self._all_steps():
            if step.metrics is None:
                continue
            for name, attr in keys.items():
                value = getattr(step.metrics, attr)
                if value is not None:
                    totals[name] = (totals[name] or 0) + value
        return TokenTotals(**totals)

    def turn_count(self) -> int:
        return sum(1 for step in self.trajectory.steps if step.source == "agent")

    def subagent_count(self) -> int:
        return len(self.trajectory.subagent_trajectories or [])

    def metrics_by_trajectory(self) -> dict[str, TrajectoryMetrics]:
        main = self.trajectory
        split = {"main": _own_metrics(main, main.agent.name if main.agent else "main")}
        for i, sub in enumerate(self.trajectory.subagent_trajectories or []):
            key = sub.trajectory_id or f"subagent_trajectories[{i}]"
            split[key] = _own_metrics(sub, (sub.agent.name if sub.agent else None) or "subagent")
        return split

    def render_steps(self) -> list[str]:
        lines: list[str] = []
        for step in self._all_steps():
            if step.reasoning_content:
                lines.append(f"[reasoning] {step.reasoning_content}")
            text = _message_text(step.message)
            if text:
                lines.append(f"[{step.source}] {text}")
            results_by_call: dict[str, ObservationResult] = {}
            if step.observation is not None:
                for result in step.observation.results:
                    if result.source_call_id:
                        results_by_call[result.source_call_id] = result
            for call in step.tool_calls or []:
                args = json.dumps(call.arguments, ensure_ascii=False, default=str)
                line = f"[tool] {call.function_name}  args: {args}"
                matched = results_by_call.get(call.tool_call_id)
                if matched is not None:
                    line += f"  result: {_result_text(matched)}"
                    if (matched.extra or {}).get("is_error") is True:
                        line += "  [ERROR]"
                lines.append(line)
        return [f"{i}. {line}" for i, line in enumerate(lines, 1)]

    def render(self) -> str:
        numbered = "\n".join(self.render_steps()) or "(none)"
        return f"Recorded steps (in order):\n{numbered}"


def trajectory_model(trajectory: Trajectory) -> str | None:
    if trajectory.agent and trajectory.agent.model_name:
        return trajectory.agent.model_name
    for step in trajectory.steps:
        if step.model_name:
            return step.model_name
    return None


def _price_steps(steps: list[Step], cost_map: dict, default_model: str | None) -> None:
    for step in steps:
        metrics = step.metrics
        # Only fill a gap: a step with token counts but no price. A zero from
        # the harness counts as a gap too: harnesses that lack rates for a
        # model (pi defaults them to 0) report 0.0 rather than omitting it.
        if metrics is None or metrics.cost_usd or metrics.prompt_tokens is None:
            continue
        model = step.model_name or default_model
        if model is None:
            continue
        rates = resolve_rates(cost_map, model)
        if rates is None:
            continue
        metrics.cost_usd = cost_usd(
            rates,
            metrics.prompt_tokens,
            metrics.completion_tokens or 0,
            metrics.cached_tokens or 0,
        )


def backfill_step_costs(trajectory: Trajectory, cost_map: dict, model: str | None = None) -> None:
    # The run's model override is the fallback when the harness didn't record a
    # model on the trajectory; a step's own model_name still wins.
    default_model = model or trajectory_model(trajectory)
    _price_steps(trajectory.steps, cost_map, default_model)
    for sub in trajectory.subagent_trajectories or []:
        _price_steps(sub.steps, cost_map, trajectory_model(sub) or default_model)


def _sum_step_costs(view: TrajectoryView) -> float | None:
    costs = [
        step.metrics.cost_usd
        for step in view._all_steps()
        if step.metrics is not None and step.metrics.cost_usd is not None
    ]
    return round(sum(costs), 10) if costs else None


def derive_metrics(view: TrajectoryView, wall_ms: int | None) -> RunMetrics:
    totals = view.token_totals()
    final = view.trajectory.final_metrics
    # Prefer a harness-reported total; otherwise sum the per-step costs.
    # backfill_step_costs prices any step the harness left unset before the
    # trajectory is stored, so the gauge and the UI cost view read the same
    # per-step numbers.
    cost = (
        final.total_cost_usd
        if (final is not None and final.total_cost_usd)
        else _sum_step_costs(view)
    )
    return RunMetrics(
        duration_ms=wall_ms,
        input_tokens=totals.input,
        output_tokens=totals.output,
        cached_tokens=totals.cached,
        cost_usd=cost,
        total_tool_calls=len(view.tool_calls()),
        errored_tool_calls=view.errored_tool_call_count(),
        subagents=view.subagent_count(),
        turns=view.turn_count(),
        by_trajectory=view.metrics_by_trajectory(),
    )
