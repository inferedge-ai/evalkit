from __future__ import annotations

import json
from bisect import bisect_right
from datetime import datetime
from typing import Any, NamedTuple

from evalkit.contracts.atif import (
    AtifAgent,
    FinalMetrics,
    Observation,
    ObservationResult,
    Step,
    StepMetrics,
    SubagentTrajectoryRef,
    ToolCall,
    Trajectory,
)
from evalkit.contracts.esf import EsfSpan, MappingDocument
from evalkit.core.traces.mapping import gen_ai_messages

ATIF_VERSION = "ATIF-v1.8"


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _as_dict(value: dict[str, Any] | str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return {"raw": value}
    return parsed if isinstance(parsed, dict) else {"raw": value}


def _output_text(value: Any) -> str | None:
    messages = gen_ai_messages(value)
    if messages is None:
        return _as_text(value)
    texts = [m.text for m in messages if m.role == "assistant" and m.text]
    return texts[-1] if texts else None


class _Tree:
    def __init__(self, spans: list[EsfSpan]):
        self.by_id = {s.span_id: s for s in spans}
        self.children: dict[str | None, list[EsfSpan]] = {}
        for s in sorted(spans, key=lambda x: x.started_at):
            parent = s.parent_id if s.parent_id in self.by_id else None
            self.children.setdefault(parent, []).append(s)

    def roots(self) -> list[EsfSpan]:
        return self.children.get(None, [])

    def subtree(self, span_id: str) -> list[EsfSpan]:
        out: list[EsfSpan] = []
        stack = [span_id]
        while stack:
            current = stack.pop()
            for child in self.children.get(current, []):
                out.append(child)
                stack.append(child.span_id)
        return out

    def ancestors(self, span: EsfSpan) -> list[EsfSpan]:
        out: list[EsfSpan] = []
        parent = span.parent_id
        while parent in self.by_id:
            out.append(self.by_id[parent])
            parent = self.by_id[parent].parent_id
        return out


def _answer_of(root: EsfSpan | None, spans: list[EsfSpan], rule: str) -> str | None:
    if rule == "root":
        return _output_text(root.output) if root is not None else None
    name, _, key = rule.partition(".")
    ordered = sorted(spans, key=lambda x: x.started_at)
    named = next((s for s in ordered if s.name == name), None)
    if named is None:
        return None
    if key and isinstance(named.output, dict):
        return _as_text(named.output.get(key))
    return _output_text(named.output)


def _window(llms: list[EsfSpan], at: datetime) -> int | None:
    index = bisect_right([s.started_at for s in llms], at) - 1
    return index if index >= 0 else None


def _result(
    span: EsfSpan, call_id: str | None, refs: dict[str, list[SubagentTrajectoryRef]]
) -> ObservationResult:
    return ObservationResult(
        source_call_id=call_id,
        content=_output_text(span.output) if span.kind == "agent" else _as_text(span.output),
        subagent_trajectory_ref=refs.get(span.span_id),
        extra={"is_error": span.error is not None},
    )


def _place(
    steps: list[Step], outcomes: list[EsfSpan], llms: list[EsfSpan]
) -> tuple[dict[int, list[tuple[str | None, EsfSpan]]], list[EsfSpan]]:
    owner = {c.tool_call_id: k for k, step in enumerate(steps) for c in step.tool_calls or []}
    placed: dict[int, list[tuple[str | None, EsfSpan]]] = {}
    claimed: set[str] = set()
    by_time: list[EsfSpan] = []
    for span in sorted(outcomes, key=lambda s: s.started_at):
        call_id = span.tool_call_id
        if call_id and call_id in owner and call_id not in claimed:
            claimed.add(call_id)
            placed.setdefault(owner[call_id], []).append((call_id, span))
        else:
            by_time.append(span)
    pending = {
        k: [c.tool_call_id for c in step.tool_calls or [] if c.tool_call_id not in claimed]
        for k, step in enumerate(steps)
    }
    unplaced: list[EsfSpan] = []
    for span in by_time:
        k = _window(llms, span.started_at)
        if k is None:
            unplaced.append(span)
            continue
        placed.setdefault(k, []).append((pending[k].pop(0) if pending[k] else None, span))
    return placed, unplaced


def _steps(
    llms: list[EsfSpan],
    outcomes: list[EsfSpan],
    refs: dict[str, list[SubagentTrajectoryRef]],
) -> tuple[list[Step], list[EsfSpan]]:
    steps: list[Step] = []
    agent_steps: list[Step] = []
    llms = sorted(llms, key=lambda s: s.started_at)
    for i, call in enumerate(llms):
        if i == 0 and call.input_messages:
            for m in call.input_messages:
                if m.role == "user":
                    steps.append(Step(step_id=0, source="user", message=m.text or ""))
                elif m.role == "system":
                    steps.append(Step(step_id=0, source="system", message=m.text or ""))
        assistant = next(
            (m for m in reversed(call.output_messages or []) if m.role == "assistant"), None
        )
        tool_calls = [
            ToolCall(tool_call_id=c.id, function_name=c.name, arguments=_as_dict(c.arguments))
            for c in (assistant.tool_calls if assistant else None) or []
        ]
        step = Step(
            step_id=0,
            timestamp=call.started_at.isoformat(),
            source="agent",
            message=(assistant.text if assistant else None) or "",
            reasoning_content=assistant.reasoning if assistant else None,
            model_name=call.model,
            metrics=StepMetrics(
                prompt_tokens=call.prompt_tokens,
                completion_tokens=call.completion_tokens,
                cached_tokens=call.cached_tokens,
                cost_usd=call.cost_usd,
                extra={"cache_creation_input_tokens": call.cache_write_tokens}
                if call.cache_write_tokens
                else None,
            ),
            tool_calls=tool_calls or None,
        )
        steps.append(step)
        agent_steps.append(step)
    placed, unplaced = _place(agent_steps, outcomes, llms)
    for k, pairs in placed.items():
        order = {c.tool_call_id: j for j, c in enumerate(agent_steps[k].tool_calls or [])}
        pairs.sort(
            key=lambda p: (order.get(p[0], len(order)) if p[0] else len(order), p[1].started_at)
        )
        agent_steps[k].observation = Observation(
            results=[_result(span, call_id, refs) for call_id, span in pairs]
        )
    return steps, unplaced


def _add[Num: (int, float)](total: Num | None, value: Num | None) -> Num | None:
    if total is None:
        return value
    if value is None:
        return total
    return total + value


def _final_metrics(steps: list[Step], subagents: list[Trajectory]) -> FinalMetrics:
    final = FinalMetrics(total_steps=len(steps))
    for m in (s.metrics for s in steps if s.metrics is not None):
        final.total_prompt_tokens = _add(final.total_prompt_tokens, m.prompt_tokens)
        final.total_completion_tokens = _add(final.total_completion_tokens, m.completion_tokens)
        final.total_cached_tokens = _add(final.total_cached_tokens, m.cached_tokens)
        final.total_cost_usd = _add(final.total_cost_usd, m.cost_usd)
    for sub in (s.final_metrics for s in subagents if s.final_metrics is not None):
        final.total_prompt_tokens = _add(final.total_prompt_tokens, sub.total_prompt_tokens)
        final.total_completion_tokens = _add(
            final.total_completion_tokens, sub.total_completion_tokens
        )
        final.total_cached_tokens = _add(final.total_cached_tokens, sub.total_cached_tokens)
        final.total_cost_usd = _add(final.total_cost_usd, sub.total_cost_usd)
    return final


class _Split(NamedTuple):
    tree: _Tree
    original_root: EsfSpan | None
    main_root: EsfSpan | None
    top_nested: list[EsfSpan]
    main: list[EsfSpan]


def _split(kept: list[EsfSpan]) -> _Split:
    tree = _Tree(kept)
    roots = tree.roots()
    original_root = roots[0] if len(roots) == 1 else None
    main_root = original_root
    if main_root is None or main_root.kind != "agent":
        top_agents = [
            s
            for s in kept
            if s.kind == "agent" and not any(a.kind == "agent" for a in tree.ancestors(s))
        ]
        if top_agents:
            main_root = min(top_agents, key=lambda s: s.started_at)
    nested_agent_ids = {s.span_id for s in kept if s.kind == "agent" and s is not main_root}
    top_nested = [
        s
        for s in kept
        if s.span_id in nested_agent_ids
        and not any(a.span_id in nested_agent_ids for a in tree.ancestors(s))
    ]
    nested_ids = {n.span_id for n in top_nested}
    nested_ids |= {s.span_id for n in top_nested for s in tree.subtree(n.span_id)}
    main = [s for s in kept if s.span_id not in nested_ids]
    return _Split(tree, original_root, main_root, top_nested, main)


def _main_llms(main: list[EsfSpan]) -> list[EsfSpan]:
    return [s for s in main if s.kind == "llm"]


def _excluded(mapping: MappingDocument | None) -> set[str]:
    return set(mapping.exclude_spans) if mapping is not None else set()


def first_llm_spans(spans: list[EsfSpan], mapping: MappingDocument | None) -> list[EsfSpan]:
    split = _split([s for s in spans if s.name not in _excluded(mapping)])
    llms = sorted(_main_llms(split.main), key=lambda s: s.started_at)
    nested = [
        first
        for n in split.top_nested
        for first in first_llm_spans([n, *split.tree.subtree(n.span_id)], mapping)
    ]
    return llms[:1] + nested


def to_atif(
    spans: list[EsfSpan],
    mapping: MappingDocument | None,
    *,
    agent_name: str,
    session_id: str | None,
    mapping_version: int | None,
    incomplete_ended_at: datetime | None = None,
) -> Trajectory:
    kept = [s for s in spans if s.name not in _excluded(mapping)]
    incomplete = sum(1 for s in kept if s.ended_at is None)
    if incomplete_ended_at is not None:
        kept = [
            s.model_copy(update={"ended_at": incomplete_ended_at}) if s.ended_at is None else s
            for s in kept
        ]
    split = _split(kept)
    tree, original_root, main_root = split.tree, split.original_root, split.main_root
    top_nested, main = split.top_nested, split.main
    tools = [
        s for s in main if s.kind == "tool" and not any(a.kind == "tool" for a in tree.ancestors(s))
    ]
    tool_ids = {t.span_id for t in tools}
    refs: dict[str, list[SubagentTrajectoryRef]] = {}
    outcomes = list(tools)
    for n in top_nested:
        ref = SubagentTrajectoryRef(trajectory_id=f"{n.trace_id}:{n.span_id}")
        spawner = next((a for a in tree.ancestors(n) if a.span_id in tool_ids), n)
        refs.setdefault(spawner.span_id, []).append(ref)
        if spawner is n:
            outcomes.append(n)
    steps, unplaced = _steps(_main_llms(main), outcomes, refs)
    rule = mapping.answer if mapping is not None else "root"
    answer = _answer_of(original_root or main_root, kept, rule)
    if answer and (not steps or steps[-1].tool_calls):
        steps.append(Step(step_id=0, source="agent", message=answer))
    for i, step in enumerate(steps, 1):
        step.step_id = i
    subagents = [
        to_atif(
            [n, *tree.subtree(n.span_id)],
            mapping,
            agent_name=n.agent_name or n.name,
            session_id=session_id,
            mapping_version=mapping_version,
        )
        for n in top_nested
    ]
    ordered_main = sorted(main, key=lambda x: x.started_at)
    first_model = next((s.model for s in ordered_main if s.kind == "llm" and s.model), None)
    extra: dict[str, Any] = {"trajectory_source": "otel", "mapping_version": mapping_version}
    if incomplete:
        extra["incomplete_spans"] = incomplete
    unplaced_tools = [s.name for s in unplaced if s.kind == "tool"]
    if unplaced_tools:
        extra["unplaced_tool_spans"] = unplaced_tools
    anchor = main_root or (kept[0] if kept else None)
    return Trajectory(
        schema_version=ATIF_VERSION,
        session_id=session_id,
        trajectory_id=f"{anchor.trace_id}:{anchor.span_id}" if anchor else None,
        agent=AtifAgent(name=agent_name, model_name=first_model),
        steps=steps,
        final_metrics=_final_metrics(steps, subagents),
        subagent_trajectories=subagents or None,
        extra=extra,
    )
