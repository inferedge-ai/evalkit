import json

import pytest

from evalkit.contracts.atif import ToolCall, validate_trajectory
from evalkit.contracts.cases import ExpectedToolCall
from evalkit.contracts.harness import Capabilities
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.evaluators import tool_trajectory as tt
from evalkit.core.evaluators.base import BUILTIN_EVALUATORS, EvalContext, execute
from evalkit.core.trajectory import TrajectoryView
from evalkit.store.base import CaseView

JUDGE = JudgeSettings(base_url="http://j", model="m")


def spec(name, args=None, args_mode="subset", match_overrides=None) -> ExpectedToolCall:
    return ExpectedToolCall(
        name=name, args=args or {}, args_mode=args_mode, match_overrides=match_overrides or {}
    )


def call(name: str, arguments: dict | None = None) -> ToolCall:
    return ToolCall(tool_call_id="c", function_name=name, arguments=arguments or {})


def calls(*items) -> list[ToolCall]:
    return [call(*item) if isinstance(item, tuple) else call(item) for item in items]


def ctx(tool_calls: list[tuple[str, dict]] | None, subagent_calls=()) -> EvalContext:
    def steps(pairs):
        return [
            {
                "step_id": i,
                "source": "agent",
                "tool_calls": [{"tool_call_id": f"c{i}", "function_name": name, "arguments": args}],
            }
            for i, (name, args) in enumerate(pairs, 1)
        ]

    view = None
    if tool_calls is not None:
        doc = {"schema_version": "ATIF-v1.8", "steps": steps(tool_calls)}
        if subagent_calls:
            doc["subagent_trajectories"] = [
                {"schema_version": "ATIF-v1.8", "steps": steps(subagent_calls)}
            ]
        view = TrajectoryView(validate_trajectory(doc))
    case = CaseView(case_ref=1, case_id="c1", content_hash="h", input={"question": "q"})
    return EvalContext(case=case, answer="a", trajectory_view=view, wall_ms=1, judge_cfg=JUDGE)


# --- tool name


def test_name_mismatch_never_matches():
    assert not tt.tool_call_matches(spec("get_news"), call("get_security"))


def test_names_compare_exactly():
    assert tt.tool_call_matches(spec("mcp__acme__get_news"), call("mcp__acme__get_news"))
    assert not tt.tool_call_matches(spec("get_news"), call("mcp__acme__get_news"))


# --- args modes


def test_subset_hit_with_extra_actual_args():
    actual = call("get_security", {"ticker": "AAPL", "fields": ["px"]})
    assert tt.tool_call_matches(spec("get_security", {"ticker": "AAPL"}), actual)


def test_subset_missing_key_fails():
    actual = call("get_security", {"fields": ["px"]})
    assert not tt.tool_call_matches(spec("get_security", {"ticker": "AAPL"}), actual)


def test_subset_value_mismatch_fails():
    actual = call("get_security", {"ticker": "MSFT"})
    assert not tt.tool_call_matches(spec("get_security", {"ticker": "AAPL"}), actual)


def test_subset_empty_args_matches_on_name():
    assert tt.tool_call_matches(spec("get_news"), call("get_news"))


def test_subset_deep_equality_on_nested_values():
    expected = spec("f", {"opts": {"a": [1, 2]}})
    assert tt.tool_call_matches(expected, call("f", {"opts": {"a": [1, 2]}, "z": 1}))
    assert not tt.tool_call_matches(expected, call("f", {"opts": {"a": [1, 3]}}))


def test_subset_deep_equality_on_list_of_dicts():
    expected = spec("f", {"filters": [{"k": "sector", "v": "tech"}]})
    assert tt.tool_call_matches(expected, call("f", {"filters": [{"k": "sector", "v": "tech"}]}))
    assert not tt.tool_call_matches(
        expected, call("f", {"filters": [{"k": "sector", "v": "energy"}]})
    )


def test_substring_case_insensitive_on_string_arg():
    actual = call("search_news", {"query": "Nvidia AI chip demand"})
    assert tt.tool_call_matches(spec("search_news", {"query": "nvidia"}, "substring"), actual)


def test_substring_searches_inside_list_value():
    actual = call("vector_search", {"queries": ["Palantir Technologies"]})
    assert tt.tool_call_matches(spec("vector_search", {"queries": "palantir"}, "substring"), actual)


def test_substring_searches_inside_nested_dict():
    actual = call(
        "stock_screener", {"structured_query": {"type": "comparison", "field": "market_cap"}}
    )
    assert tt.tool_call_matches(
        spec("stock_screener", {"structured_query": "market_cap"}, "substring"), actual
    )


def test_substring_no_match_fails():
    actual = call("search_news", {"query": "Fed rate cuts"})
    assert not tt.tool_call_matches(spec("search_news", {"query": "nvidia"}, "substring"), actual)


def test_substring_missing_key_fails():
    actual = call("search_news", {"depth": "detailed"})
    assert not tt.tool_call_matches(spec("search_news", {"query": "nvidia"}, "substring"), actual)


def test_exact_requires_identical_args():
    expected = spec("make_chart", {"type": "line"}, args_mode="exact")
    assert tt.tool_call_matches(expected, call("make_chart", {"type": "line"}))
    assert not tt.tool_call_matches(expected, call("make_chart", {"type": "line", "points": 30}))


def test_exact_deep_equality_on_nested_values():
    expected = spec("f", {"opts": {"a": [1, 2]}}, args_mode="exact")
    assert tt.tool_call_matches(expected, call("f", {"opts": {"a": [1, 2]}}))
    assert not tt.tool_call_matches(expected, call("f", {"opts": {"a": [1, 2]}, "z": 1}))


def test_ignore_matches_on_name_only():
    expected = spec("run_sql", {"query": "X"}, args_mode="ignore")
    assert tt.tool_call_matches(expected, call("run_sql", {"query": "Y"}))


def test_match_overrides_mix_equals_and_substring():
    expected = spec(
        "vector_search",
        {"table": "tags", "queries": "semiconductor"},
        match_overrides={"queries": "substring"},
    )
    assert tt.tool_call_matches(
        expected, call("vector_search", {"table": "tags", "queries": ["Semiconductor ETFs"]})
    )
    assert not tt.tool_call_matches(
        expected, call("vector_search", {"table": "entities", "queries": ["Semis"]})
    )
    assert not tt.tool_call_matches(
        expected, call("vector_search", {"table": "tags", "queries": ["banks"]})
    )


def test_match_overrides_equals_inside_substring_mode():
    expected = spec(
        "get_bond_vwap",
        {"series": "SDL", "isin": "INE"},
        args_mode="substring",
        match_overrides={"series": "equals"},
    )
    assert tt.tool_call_matches(expected, call("get_bond_vwap", {"series": "SDL", "isin": "INE1"}))
    assert not tt.tool_call_matches(
        expected, call("get_bond_vwap", {"series": "SDL-X", "isin": "INE1"})
    )


# --- trajectory matching


def test_unordered_all_satisfied_any_order():
    result = tt.match_trajectory([spec("b"), spec("a")], calls("a", "b"), ordered=False)
    assert result.satisfied == 2 and result.total == 2
    assert all(d.matched for d in result.diagnostics)


def test_unordered_partial_with_name_miss():
    result = tt.match_trajectory([spec("a"), spec("c")], calls("a", "b"), ordered=False)
    assert result.satisfied == 1
    [miss] = [d for d in result.diagnostics if not d.matched]
    assert miss.expected.name == "c" and miss.reason == "name miss"


def test_unordered_args_mismatch_reason():
    result = tt.match_trajectory([spec("a", {"x": 1})], calls(("a", {"x": 2})), ordered=False)
    assert result.satisfied == 0
    assert result.diagnostics[0].reason == "args mismatch"


def test_unordered_distinct_calls_for_repeated_tool():
    expected = [spec("a", {"x": 1}), spec("a", {"x": 2})]
    assert tt.match_trajectory(expected, calls(("a", {"x": 1})), ordered=False).satisfied == 1
    both = tt.match_trajectory(expected, calls(("a", {"x": 1}), ("a", {"x": 2})), ordered=False)
    assert both.satisfied == 2


def test_unordered_finds_maximum_matching_not_greedy():
    expected = [spec("a", args_mode="ignore"), spec("a", {"x": 1})]
    result = tt.match_trajectory(expected, calls(("a", {"x": 1}), ("a", {"x": 2})), ordered=False)
    assert result.satisfied == 2


def test_unordered_no_available_call_when_consumed():
    expected = [spec("a", {"x": 1}), spec("a", {"x": 1})]
    result = tt.match_trajectory(expected, calls(("a", {"x": 1})), ordered=False)
    assert result.satisfied == 1
    [miss] = [d for d in result.diagnostics if not d.matched]
    assert miss.reason == "no available matching call"


def test_ordered_in_order_passes():
    result = tt.match_trajectory([spec("a"), spec("b")], calls("a", "x", "b"), ordered=True)
    assert result.satisfied == 2


def test_ordered_out_of_order_partial():
    result = tt.match_trajectory([spec("a"), spec("b")], calls("b", "a"), ordered=True)
    assert result.satisfied == 1
    [miss] = [d for d in result.diagnostics if not d.matched]
    assert miss.reason == "out of order"


# --- tool_trajectory_match evaluator


async def test_tool_trajectory_match_fractional_score_and_verdicts():
    config = {
        "expected": [
            {"name": "get_security", "args": {"ticker": "AAPL"}},
            {"name": "make_chart", "args": {"type": "bar"}},
            {"name": "get_news"},
        ]
    }
    [row] = await tt.tool_trajectory_match(
        ctx([("get_security", {"ticker": "AAPL"}), ("make_chart", {"type": "line"})]),
        config,
    )
    assert row.name == "tool_trajectory_match"
    assert row.score == pytest.approx(1 / 3)
    body = json.loads(row.explanation)
    assert body["satisfied"] == 1 and body["total"] == 3 and body["ordered"] is False
    assert [(v["expected"], v["verdict"], v["note"]) for v in body["verdicts"]] == [
        ("get_security", "satisfied", ""),
        ("make_chart", "unsatisfied", "args mismatch"),
        ("get_news", "unsatisfied", "name miss"),
    ]
    assert body["verdicts"][1]["args"] == {"type": "bar"}


async def test_tool_trajectory_match_reads_subagent_calls():
    [row] = await tt.tool_trajectory_match(
        ctx([("plan", {})], subagent_calls=[("get_news", {})]),
        {"expected": [{"name": "get_news"}]},
    )
    assert row.score == 1.0


async def test_tool_trajectory_match_ordered():
    [row] = await tt.tool_trajectory_match(
        ctx([("b", {}), ("a", {})]),
        {"ordered": True, "expected": [{"name": "a"}, {"name": "b"}]},
    )
    assert row.score == 0.5


async def test_tool_trajectory_match_skips_without_trajectory():
    [row] = await tt.tool_trajectory_match(ctx(None), {"expected": [{"name": "a"}]})
    assert row.score is None and row.label == "skip"


# --- required_tools_called evaluator


async def test_required_tools_called_recall_and_verdicts():
    [row] = await tt.required_tools_called(
        ctx([("get_news", {}), ("search", {}), ("search", {})]),
        {"tools": ["get_news", "get_bond_trades"]},
    )
    assert row.name == "required_tools_called"
    assert row.score == 0.5
    body = json.loads(row.explanation)
    assert body["invoked"] == 1 and body["total"] == 2
    assert body["verdicts"] == [
        {"tool": "get_news", "verdict": "satisfied"},
        {"tool": "get_bond_trades", "verdict": "missing"},
    ]


async def test_required_tools_called_compares_names_exactly():
    [row] = await tt.required_tools_called(
        ctx([("mcp__acme__get_news", {})]),
        {"tools": ["mcp__acme__get_news", "get_news", "get_news"]},
    )
    assert row.score == 0.5
    assert json.loads(row.explanation)["total"] == 2


async def test_required_tools_called_skips_without_trajectory():
    [row] = await tt.required_tools_called(ctx(None), {"tools": ["a"]})
    assert row.score is None and row.label == "skip"


# --- registration and gating


@pytest.mark.parametrize("name", ["tool_trajectory_match", "required_tools_called"])
def test_registered_requires(name):
    assert BUILTIN_EVALUATORS[name].requires == frozenset({"trajectory", "tool_calls"})


async def test_neutral_skip_without_tool_calls_capability():
    caps = Capabilities.model_validate(
        {"agent": "x", "provides": {"trajectory": True, "tool_calls": False}}
    )
    rows = await execute(
        BUILTIN_EVALUATORS["required_tools_called"], ctx([("a", {})]), {"tools": ["a"]}, caps
    )
    assert rows[0].score is None and rows[0].label == "skip"
