"""Deterministic checks over the tool calls in the recorded trajectory.

tool_trajectory_match:  score = matched / len(expected)
    unordered: matched = size of a maximum one-to-one matching between
               expected entries and actual calls
    ordered:   matched = longest common subsequence of (expected, actual)

required_tools_called:  score = |required ∩ called| / |required|
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evalkit.contracts.atif import ToolCall
from evalkit.contracts.cases import ExpectedToolCall, RequiredToolsCalled, ToolTrajectoryMatch
from evalkit.core.evaluators.base import EvalContext, builtin_evaluator, neutral_skip
from evalkit.store.base import ScoreRow

REQUIRES = {"trajectory", "tool_calls"}


@dataclass
class EntryDiagnostic:
    expected: ExpectedToolCall
    matched: bool
    reason: str = ""


@dataclass
class TrajectoryMatchResult:
    satisfied: int
    total: int
    diagnostics: list[EntryDiagnostic]


def _searchable_text(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text.lower()


def _arg_value_matches(mode: str, actual_value: Any, expected_value: Any) -> bool:
    if mode == "substring":
        return str(expected_value).lower() in _searchable_text(actual_value)
    return bool(actual_value == expected_value)


def tool_call_matches(expected: ExpectedToolCall, actual: ToolCall) -> bool:
    if expected.name != actual.function_name:
        return False
    if expected.args_mode == "ignore":
        return True
    if expected.args_mode == "exact":
        return actual.arguments == expected.args
    default_mode = "substring" if expected.args_mode == "substring" else "equals"
    for key, value in expected.args.items():
        if key not in actual.arguments:
            return False
        mode = expected.match_overrides.get(key, default_mode)
        if not _arg_value_matches(mode, actual.arguments[key], value):
            return False
    return True


def _diagnose(expected: ExpectedToolCall, actual: list[ToolCall], *, ordered: bool) -> str:
    if not any(a.function_name == expected.name for a in actual):
        return "name miss"
    if not any(tool_call_matches(expected, a) for a in actual):
        return "args mismatch"
    return "out of order" if ordered else "no available matching call"


def _unordered_matched(expected: list[ExpectedToolCall], actual: list[ToolCall]) -> set[int]:
    adj = [[j for j, a in enumerate(actual) if tool_call_matches(e, a)] for e in expected]
    match_right: dict[int, int] = {}

    def augment(i: int, visited: set[int]) -> bool:
        for j in adj[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in match_right or augment(match_right[j], visited):
                match_right[j] = i
                return True
        return False

    for i in range(len(expected)):
        augment(i, set())
    return set(match_right.values())


def _ordered_matched(expected: list[ExpectedToolCall], actual: list[ToolCall]) -> set[int]:
    m, n = len(expected), len(actual)
    hit = [[tool_call_matches(e, a) for a in actual] for e in expected]
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if hit[i - 1][j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    matched: set[int] = set()
    i, j = m, n
    while i > 0 and j > 0:
        if hit[i - 1][j - 1] and dp[i][j] == dp[i - 1][j - 1] + 1:
            matched.add(i - 1)
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return matched


def match_trajectory(
    expected: list[ExpectedToolCall], actual: list[ToolCall], *, ordered: bool
) -> TrajectoryMatchResult:
    matched = (
        _ordered_matched(expected, actual) if ordered else _unordered_matched(expected, actual)
    )
    diagnostics = [
        EntryDiagnostic(
            expected=e,
            matched=i in matched,
            reason="" if i in matched else _diagnose(e, actual, ordered=ordered),
        )
        for i, e in enumerate(expected)
    ]
    return TrajectoryMatchResult(
        satisfied=len(matched), total=len(expected), diagnostics=diagnostics
    )


@builtin_evaluator("tool_trajectory_match", requires=REQUIRES)
async def tool_trajectory_match(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    cfg = ToolTrajectoryMatch.model_validate(config)
    if ctx.trajectory_view is None:
        return [neutral_skip("tool_trajectory_match", "no trajectory recorded for this run")]
    result = match_trajectory(cfg.expected, ctx.trajectory_view.tool_calls(), ordered=cfg.ordered)
    verdicts = [
        {
            "index": index,
            "expected": d.expected.name,
            "args": d.expected.args,
            "args_mode": d.expected.args_mode,
            "verdict": "satisfied" if d.matched else "unsatisfied",
            "note": d.reason,
        }
        for index, d in enumerate(result.diagnostics, 1)
    ]
    explanation = {
        "satisfied": result.satisfied,
        "total": result.total,
        "ordered": cfg.ordered,
        "verdicts": verdicts,
    }
    return [
        ScoreRow(
            name="tool_trajectory_match",
            score=result.satisfied / result.total,
            explanation=json.dumps(explanation, ensure_ascii=False, default=str),
        )
    ]


@builtin_evaluator("required_tools_called", requires=REQUIRES)
async def required_tools_called(ctx: EvalContext, config: dict[str, Any]) -> list[ScoreRow]:
    cfg = RequiredToolsCalled.model_validate(config)
    if ctx.trajectory_view is None:
        return [neutral_skip("required_tools_called", "no trajectory recorded for this run")]
    required = list(dict.fromkeys(cfg.tools))
    called = {c.function_name for c in ctx.trajectory_view.tool_calls()}
    verdicts = [
        {"tool": tool, "verdict": "satisfied" if tool in called else "missing"} for tool in required
    ]
    invoked = sum(1 for tool in required if tool in called)
    explanation = {"invoked": invoked, "total": len(required), "verdicts": verdicts}
    return [
        ScoreRow(
            name="required_tools_called",
            score=invoked / len(required),
            explanation=json.dumps(explanation, ensure_ascii=False),
        )
    ]
