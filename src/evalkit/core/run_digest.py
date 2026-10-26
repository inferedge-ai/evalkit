import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import datetime, tzinfo
from typing import Any, Literal, get_args

from pydantic import BaseModel

from evalkit.contracts.atif import ContentPart, ObservationResult, Step, Trajectory
from evalkit.core.trajectory import TrajectoryView, derive_metrics, trajectory_model

CostSource = Literal["reported", "estimated", "mixed"]
TurnStatus = Literal["ok", "failed", "no_answer"]

QUESTION_CHARS = 300
ANSWER_CHARS = 600
ERROR_CHARS = 300


def clip(text: str | None, limit: int) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return f"{text[:limit]}…[+{len(text) - limit} chars]"


def content_text(content: str | list[ContentPart] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return " ".join(part.text for part in content if part.type == "text" and part.text)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seconds(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return round((end - start).total_seconds(), 3)


def _step_extra(step: Step) -> dict:
    return step.model_extra.get("extra") or {} if step.model_extra else {}


def _is_error(result: ObservationResult) -> bool:
    return (result.extra or {}).get("is_error") is True


class ToolErrors(BaseModel):
    tool: str
    count: int
    message: str | None = None


class ModelCost(BaseModel):
    model: str
    cost_usd: float
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    steps: int


class ToolCallGroup(BaseModel):
    tool: str
    count: int
    arguments: dict[str, Any]


class Turn(BaseModel):
    index: int
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    status: TurnStatus
    error: str | None = None
    question: str | None
    answer: str | None
    cost_usd: float | None
    agent_steps: int
    tool_calls: int
    tool_errors: int


class RunDigest(BaseModel):
    run_id: str
    started_at: datetime | None
    ended_at: datetime | None
    duration_seconds: float | None
    wall_seconds: float | None
    turns: int
    failed_turns: int
    failures: int
    agent_steps: int
    cost_usd: float | None
    cost_source: CostSource | None
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    tool_calls: int
    tool_errors: int
    tool_errors_by_tool: list[ToolErrors]
    tool_call_groups: list[ToolCallGroup]
    cost_by_model: list[ModelCost]
    models: list[str]
    subagents: int
    opening_question: str | None
    final_answer: str | None


def _turn_groups(steps: list[Step]) -> list[list[Step]]:
    groups: list[list[Step]] = []
    for step in steps:
        if step.source == "user" or not groups:
            groups.append([step])
        else:
            groups[-1].append(step)
    return groups


def _turn(index: int, steps: list[Step]) -> Turn:
    question = next((content_text(s.message) for s in steps if s.source == "user"), None)
    answers = [
        content_text(s.message)
        for s in steps
        if s.source == "agent" and not s.tool_calls and content_text(s.message)
    ]
    failing = next((s for s in steps if _step_extra(s).get("is_error") is True), None)
    error = None
    if failing is not None:
        error = _step_extra(failing).get("error") or content_text(failing.message) or "error"
    status: TurnStatus = "failed" if failing is not None else "ok" if answers else "no_answer"
    costs = [s.metrics.cost_usd for s in steps if s.metrics and s.metrics.cost_usd is not None]
    stamps = [t for t in (_parse_ts(s.timestamp) for s in steps) if t is not None]
    started = stamps[0] if stamps else None
    ended = stamps[-1] if stamps else None
    return Turn(
        index=index,
        started_at=started,
        ended_at=ended,
        duration_seconds=_seconds(started, ended),
        status=status,
        error=clip(error, ERROR_CHARS),
        question=clip(question, QUESTION_CHARS),
        answer=clip(answers[-1] if answers else None, ANSWER_CHARS),
        cost_usd=round(sum(costs), 10) if costs else None,
        agent_steps=sum(1 for s in steps if s.source == "agent"),
        tool_calls=sum(len(s.tool_calls or []) for s in steps),
        tool_errors=sum(
            sum(1 for r in s.observation.results if _is_error(r)) for s in steps if s.observation
        ),
    )


def turns(trajectory: Trajectory) -> list[Turn]:
    return [_turn(i, group) for i, group in enumerate(_turn_groups(trajectory.steps), 1)]


def _all_steps(trajectory: Trajectory) -> Iterable[Step]:
    yield from trajectory.steps
    for sub in trajectory.subagent_trajectories or []:
        yield from _all_steps(sub)


TOOL_ARGUMENT_CHARS = 120
TOOL_CALL_GROUPS_MAX = 100


def _short(value: Any) -> Any:
    # Strings keep both ends: a path's file extension and a command's head both matter.
    if isinstance(value, str):
        if len(value) <= TOOL_ARGUMENT_CHARS:
            return value
        half = TOOL_ARGUMENT_CHARS // 2
        return f"{value[:half]}…{value[-half:]}"
    text = json.dumps(value, default=str)
    return value if len(text) <= TOOL_ARGUMENT_CHARS else clip(text, TOOL_ARGUMENT_CHARS)


# A step without a model counts under its trajectory's, the rule the cost
# backfill prices it by; the sum misses a run whose harness reported only a total.
def cost_by_model(trajectory: Trajectory) -> list[ModelCost]:
    totals: dict[str, ModelCost] = {}

    def walk(t: Trajectory, inherited: str | None) -> None:
        default = trajectory_model(t) or inherited
        for step in t.steps:
            if step.metrics is None or step.metrics.cost_usd is None:
                continue
            model = step.model_name or default or "unknown"
            row = totals.setdefault(
                model,
                ModelCost(
                    model=model,
                    cost_usd=0.0,
                    input_tokens=0,
                    output_tokens=0,
                    cached_tokens=0,
                    steps=0,
                ),
            )
            row.cost_usd += step.metrics.cost_usd
            row.input_tokens += step.metrics.prompt_tokens or 0
            row.output_tokens += step.metrics.completion_tokens or 0
            row.cached_tokens += step.metrics.cached_tokens or 0
            row.steps += 1
        for sub in t.subagent_trajectories or []:
            walk(sub, default)

    walk(trajectory, None)
    for row in totals.values():
        row.cost_usd = round(row.cost_usd, 10)
    return sorted(totals.values(), key=lambda r: -r.cost_usd)


def tool_call_groups(trajectory: Trajectory) -> list[ToolCallGroup]:
    counts: dict[str, int] = defaultdict(int)
    shapes: dict[str, tuple[str, dict[str, Any]]] = {}
    for step in _all_steps(trajectory):
        for call in step.tool_calls or []:
            arguments = {k: _short(v) for k, v in (call.arguments or {}).items()}
            key = json.dumps([call.function_name, arguments], sort_keys=True, default=str)
            counts[key] += 1
            shapes[key] = (call.function_name, arguments)
    ranked = sorted(counts, key=lambda k: -counts[k])[:TOOL_CALL_GROUPS_MAX]
    return [
        ToolCallGroup(tool=shapes[k][0], count=counts[k], arguments=shapes[k][1]) for k in ranked
    ]


def tool_errors_by_tool(trajectory: Trajectory) -> list[ToolErrors]:
    counts: dict[str, int] = defaultdict(int)
    messages: dict[str, str] = {}
    for step in _all_steps(trajectory):
        if step.observation is None:
            continue
        names = {call.tool_call_id: call.function_name for call in step.tool_calls or []}
        for result in step.observation.results:
            if not _is_error(result):
                continue
            tool = names.get(result.source_call_id or "", "unknown")
            counts[tool] += 1
            if tool not in messages:
                error_type = (result.extra or {}).get("error_type")
                text = content_text(result.content) or error_type or ""
                messages[tool] = clip(text, ERROR_CHARS) or ""
    return [
        ToolErrors(tool=tool, count=count, message=messages.get(tool) or None)
        for tool, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def priced_steps(trajectory: Trajectory) -> set[int]:
    return {
        id(step)
        for step in _all_steps(trajectory)
        if step.metrics is not None and step.metrics.cost_usd
    }


def cost_source(trajectory: Trajectory, reported: set[int]) -> CostSource | None:
    priced = priced_steps(trajectory)
    final = trajectory.final_metrics
    if final is not None and final.total_cost_usd:
        return "reported"
    if not priced:
        return None
    if priced <= reported:
        return "reported"
    if reported & priced:
        return "mixed"
    return "estimated"


def digest(run_id: str, trajectory: Trajectory, reported: set[int]) -> RunDigest:
    view = TrajectoryView(trajectory)
    metrics = derive_metrics(view, None)
    run_turns = turns(trajectory)
    durations = [t.duration_seconds for t in run_turns if t.duration_seconds is not None]
    stamps = [t for t in (_parse_ts(s.timestamp) for s in trajectory.steps) if t is not None]
    agent_model = trajectory.agent.model_name if trajectory.agent else None
    models = sorted(
        {s.model_name for s in _all_steps(trajectory) if s.model_name}
        | ({agent_model} if agent_model else set())
    )
    final_answer = next((t.answer for t in reversed(run_turns) if t.answer), None)
    return RunDigest(
        run_id=run_id,
        started_at=stamps[0] if stamps else None,
        ended_at=stamps[-1] if stamps else None,
        duration_seconds=round(sum(durations), 3) if durations else None,
        wall_seconds=_seconds(stamps[0], stamps[-1]) if stamps else None,
        turns=sum(1 for s in trajectory.steps if s.source == "user"),
        failed_turns=sum(1 for t in run_turns if t.status == "failed"),
        failures=sum(1 for s in trajectory.steps if _step_extra(s).get("is_error") is True),
        agent_steps=metrics.turns,
        cost_usd=metrics.cost_usd,
        cost_source=cost_source(trajectory, reported),
        input_tokens=metrics.input_tokens,
        output_tokens=metrics.output_tokens,
        cached_tokens=metrics.cached_tokens,
        tool_calls=metrics.total_tool_calls,
        tool_errors=metrics.errored_tool_calls,
        tool_errors_by_tool=tool_errors_by_tool(trajectory),
        tool_call_groups=tool_call_groups(trajectory),
        cost_by_model=cost_by_model(trajectory),
        models=models,
        subagents=metrics.subagents,
        opening_question=run_turns[0].question if run_turns else None,
        final_answer=final_answer,
    )


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


GroupBy = Literal["none", "day", "tool", "user", "source"]
GROUP_BY_VALUES: tuple[str, ...] = get_args(GroupBy)


class StatsInput(BaseModel):
    digest: RunDigest
    turn_durations: list[float]
    source: str
    user_id: str | None
    created_at: datetime | None


class GroupStats(BaseModel):
    group: dict[str, str]
    runs: int
    runs_with_failure: int
    turns: int
    failed_turns: int
    failures: int
    cost_usd: float
    runs_with_estimated_cost: int
    runs_without_cost: int
    turn_p50_seconds: float | None
    turn_p90_seconds: float | None
    tool_errors: int
    runs_with_tool_errors: int
    sample_error: str | None = None


def _day(moment: datetime | None, zone: tzinfo) -> str:
    return moment.astimezone(zone).date().isoformat() if moment else "unknown"


def _summarize(
    group: dict[str, str], items: list[StatsInput], tool_errors: Callable[[RunDigest], int]
) -> GroupStats:
    digests = [i.digest for i in items]
    durations = [d for i in items for d in i.turn_durations]
    return GroupStats(
        group=group,
        runs=len(items),
        runs_with_failure=sum(1 for d in digests if d.failures),
        turns=sum(d.turns for d in digests),
        failed_turns=sum(d.failed_turns for d in digests),
        failures=sum(d.failures for d in digests),
        cost_usd=round(sum(d.cost_usd or 0.0 for d in digests), 10),
        runs_with_estimated_cost=sum(1 for d in digests if d.cost_source in ("estimated", "mixed")),
        runs_without_cost=sum(1 for d in digests if d.cost_usd is None),
        turn_p50_seconds=percentile(durations, 0.5),
        turn_p90_seconds=percentile(durations, 0.9),
        tool_errors=sum(tool_errors(d) for d in digests),
        runs_with_tool_errors=sum(1 for d in digests if tool_errors(d)),
    )


def _all_tool_errors(digest: RunDigest) -> int:
    return digest.tool_errors


def _tool_rows(items: list[StatsInput]) -> list[GroupStats]:
    by_tool: dict[str, list[StatsInput]] = defaultdict(list)
    messages: dict[str, str | None] = {}
    for item in items:
        for entry in item.digest.tool_errors_by_tool:
            by_tool[entry.tool].append(item)
            messages.setdefault(entry.tool, entry.message)
    rows = []
    for tool, tool_items in by_tool.items():

        def count(digest: RunDigest, tool: str = tool) -> int:
            return sum(e.count for e in digest.tool_errors_by_tool if e.tool == tool)

        row = _summarize({"tool": tool}, tool_items, count)
        row.sample_error = messages[tool]
        rows.append(row)
    return sorted(rows, key=lambda r: (-r.tool_errors, r.group["tool"]))


def group_stats(items: list[StatsInput], zone: tzinfo, group_by: GroupBy) -> list[GroupStats]:
    if group_by == "none":
        return [_summarize({}, items, _all_tool_errors)]
    if group_by == "tool":
        return _tool_rows(items)
    keys: dict[str, Callable[[StatsInput], str]] = {
        "day": lambda i: _day(i.created_at or i.digest.started_at, zone),
        "user": lambda i: i.user_id or "unknown",
        "source": lambda i: i.source,
    }
    groups: dict[str, list[StatsInput]] = defaultdict(list)
    for item in items:
        groups[keys[group_by](item)].append(item)
    rows = [_summarize({group_by: k}, v, _all_tool_errors) for k, v in groups.items()]
    if group_by == "day":
        return sorted(rows, key=lambda r: r.group["day"])
    return sorted(rows, key=lambda r: (-r.runs, r.group[group_by]))
