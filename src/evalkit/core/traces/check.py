from __future__ import annotations

from typing import Any

from evalkit.core.traces.mapping import CANONICAL_ROLES

MARK = {"pass": "✓", "warn": "!", "fail": "✗"}


def _line(status: str, label: str, detail: str) -> str:
    return f"  {MARK[status]} {label:<14} {detail}"


def _trajectories(trajectory: dict) -> list[dict]:
    out = [trajectory] if trajectory else []
    for sub in trajectory.get("subagent_trajectories") or []:
        out.extend(_trajectories(sub))
    return out


def _calls(trajectory: dict) -> list[dict]:
    return [c for step in trajectory.get("steps") or [] for c in step.get("tool_calls") or []]


def _llm_steps(trajectory: dict) -> int:
    return sum(
        1
        for s in trajectory.get("steps") or []
        if s.get("source") == "agent" and s.get("timestamp")
    )


def _root_line(spans: list[dict]) -> str:
    root = next((s for s in spans if s["parent_id"] is None), None)
    if root is not None:
        ended = "ended" if root["ended_at"] else "not ended"
        return _line("pass", "root", f'"{root["name"]}" ({root["kind"]}), {ended}')
    orphans = sum(1 for s in spans if s["parent_id"] is not None)
    return _line(
        "fail",
        "root",
        f"no root span (parent_span_id null) in this session: {orphans} spans were grouped "
        "without it; put the session id on the root span, or stamp it on every span",
    )


def _llm_line(llms: list[dict]) -> str:
    if not llms:
        return _line("warn", "llm", "no llm spans; check the kind rule")
    models = sorted({s["model"] for s in llms if s.get("model")})
    bare = sum(1 for s in llms if not s.get("input_messages") and not s.get("output_messages"))
    keys = ("prompt_tokens", "completion_tokens", "cached_tokens")
    totals = {k: sum(s.get(k) or 0 for s in llms) for k in keys}
    detail = (
        f"{len(llms)} spans, model {', '.join(models) or 'unknown'}, tokens "
        f"in={totals['prompt_tokens']} out={totals['completion_tokens']} "
        f"cached={totals['cached_tokens']}"
    )
    if bare:
        detail += f", {bare} without messages (is message content capture on?)"
    no_tokens = not totals["prompt_tokens"] and not totals["completion_tokens"]
    if no_tokens:
        detail += ", no token counts (check the tokens rule)"
    roles = {m.get("role") for s in llms for m in s.get("output_messages") or []}
    unknown = sorted(map(str, roles - CANONICAL_ROLES))
    if unknown:
        detail += f", output messages with unknown roles are dropped ({', '.join(unknown)})"
    return _line("warn" if bare or no_tokens or unknown else "pass", "llm", detail)


def _tools_line(tools: list[dict], every: list[dict]) -> str:
    calls = [c for t in every for c in _calls(t)]
    call_ids = {c["tool_call_id"] for c in calls}
    matched = len({s.get("tool_call_id") for s in tools if s.get("tool_call_id")} & call_ids)
    unplaced = [n for t in every for n in (t.get("extra") or {}).get("unplaced_tool_spans") or []]
    detail = f"{len(tools)} spans, {matched} matched to calls by id"
    if unplaced:
        detail += f", {len(unplaced)} in no step ({', '.join(unplaced[:2])})"
    no_calls = bool(tools) and not calls
    if no_calls:
        detail += ", but the trajectory has 0 tool calls (do the llm output messages carry them?)"
    ok = tools and not unplaced and not no_calls
    return _line("pass" if ok else "warn", "tools", detail)


def _shape_lines(llms: list[dict], trajectory: dict, every: list[dict]) -> list[str]:
    lines: list[str] = []
    steps = sum(_llm_steps(t) for t in every)
    if steps and len(llms) > steps:
        lines.append(
            _line(
                "warn",
                "steps",
                f"{len(llms)} llm spans but {steps} llm steps; check exclude_spans and the "
                "kind rule",
            )
        )
    subs = trajectory.get("subagent_trajectories") or []
    main_work = _llm_steps(trajectory) + len(_calls(trajectory))
    if subs and not main_work and any(_llm_steps(t) for t in every[1:]):
        lines.append(
            _line(
                "warn",
                "subagents",
                f"the main agent has no llm calls and {len(subs)} subagents hold all of them; "
                "is an outer wrapper span the main agent? set main_agent or exclude_spans",
            )
        )
    return lines


def render_check(session: dict[str, Any]) -> tuple[list[str], bool]:
    spans = session.get("esf") or []
    esf = [s for s in spans if not s.get("excluded")]
    traces = {s["trace_id"] for s in spans}
    plural = "s" if len(traces) != 1 else ""
    settled = "settled" if session["settled"] else "not settled yet"
    lines = [
        _line(
            "pass" if session["settled"] else "warn",
            "session",
            f"{session['span_count']} spans in {len(traces)} trace{plural}, {settled}",
        ),
        _root_line(spans),
    ]
    version = session.get("mapping_version")
    lines.append(
        _line("pass", "mapping", f"v{version}")
        if version
        else _line("warn", "mapping", "no active mapping; the agent's spans are unmapped")
    )
    llms = [s for s in esf if s["kind"] == "llm"]
    lines.append(_llm_line(llms))
    trajectory = session.get("trajectory") or {}
    every = _trajectories(trajectory)
    lines.append(_tools_line([s for s in esf if s["kind"] == "tool"], every))
    unmapped = session.get("unmapped_attributes") or []
    if unmapped:
        more = f" (+{len(unmapped) - 2})" if len(unmapped) > 2 else ""
        lines.append(_line("warn", "unmapped", ", ".join(unmapped[:2]) + more))
    else:
        lines.append(_line("pass", "unmapped", "every attribute was read"))
    if trajectory:
        subs = len(trajectory.get("subagent_trajectories") or [])
        lines.append(
            _line(
                "pass",
                "trajectory",
                f"{trajectory.get('schema_version')}, {len(trajectory.get('steps') or [])} "
                f"steps, {len(_calls(trajectory))} tool calls, {subs} subagents",
            )
        )
        lines += _shape_lines(llms, trajectory, every)
    else:
        lines.append(_line("fail", "trajectory", "no trajectory"))
    return lines, any(s["parent_id"] is None for s in spans)
