from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from evalkit.contracts.atif import validate_trajectory
from evalkit.core.costmap import cost_usd, resolve_rates
from evalkit.core.run_digest import (
    StatsInput,
    digest,
    group_stats,
    percentile,
    priced_steps,
    tool_call_groups,
    turns,
)
from evalkit.core.trajectory import backfill_step_costs
from tests.mcp_trajectory import chat_session

COST_MAP = {"judge-model": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}


def priced():
    trajectory = validate_trajectory(chat_session())
    reported = priced_steps(trajectory)
    backfill_step_costs(trajectory, COST_MAP)
    return trajectory, reported


def test_turns_split_on_user_steps_and_mark_failures():
    trajectory, _ = priced()
    first, second = turns(trajectory)
    assert (first.status, first.duration_seconds, first.tool_errors) == ("ok", 30.0, 3)
    assert first.question == "Which documents mention the refund policy?"
    assert first.answer == "Two documents mention it."
    assert second.status == "failed"
    assert second.error == "This turn ran past its 60-minute limit and was stopped."
    assert second.duration_seconds == 100.0 and second.answer is None


def test_digest_groups_tool_errors_and_says_where_cost_came_from():
    trajectory, reported = priced()
    result = digest("sess-chat", trajectory, reported)
    assert (result.turns, result.failed_turns, result.agent_steps) == (2, 1, 2)
    assert result.duration_seconds == 130.0 and result.wall_seconds == 400.0
    expected = 0.5 + cost_usd(resolve_rates(COST_MAP, "judge-model"), 2000, 200, 0)
    assert result.cost_usd == pytest.approx(expected)
    assert result.cost_source == "mixed"
    assert [(e.tool, e.count, e.message) for e in result.tool_errors_by_tool] == [
        ("search_docs", 2, "unknown_column: name"),
        ("Bash", 1, "Shell command failed"),
    ]
    assert result.opening_question == "Which documents mention the refund policy?"
    assert result.final_answer == "Two documents mention it."
    assert result.models == ["judge-model"]


def test_failures_count_every_failure_step_not_just_failed_turns():
    # A message that failed before reaching the transcript has no user step of
    # its own: its failure lands in the previous turn, which then holds two.
    doc = chat_session()
    failure = next(s for s in doc["steps"] if (s.get("extra") or {}).get("is_error"))
    doc["steps"].append({**failure, "step_id": len(doc["steps"]) + 1})
    trajectory = validate_trajectory(doc)
    result = digest("sess-chat", trajectory, set())
    assert (result.failed_turns, result.failures) == (1, 2)
    (row,) = group_stats(
        [StatsInput(digest=result, turn_durations=[], source="app", user_id="u", created_at=None)],
        UTC,
        "none",
    )
    assert (row.runs_with_failure, row.failed_turns, row.failures) == (1, 1, 2)


def test_long_string_arguments_keep_both_ends():
    doc = chat_session()
    path = "/tmp/workspaces/" + "x" * 200 + "/report.xlsx"
    step = next(s for s in doc["steps"] if s.get("tool_calls"))
    step["tool_calls"][0]["arguments"] = {"file_path": path}
    groups = tool_call_groups(validate_trajectory(doc))
    (kept,) = [g.arguments["file_path"] for g in groups if "file_path" in g.arguments]
    assert kept.startswith("/tmp/workspaces/") and kept.endswith("/report.xlsx") and "…" in kept


def test_cost_by_model_sums_to_the_run_cost():
    trajectory, reported = priced()
    result = digest("sess-chat", trajectory, reported)
    assert result.cost_by_model
    assert sum(m.cost_usd for m in result.cost_by_model) == pytest.approx(result.cost_usd)
    assert {m.model for m in result.cost_by_model} <= set(result.models) | {"unknown"}
    assert sum(m.steps for m in result.cost_by_model) == len(
        [s for s in trajectory.steps if s.metrics and s.metrics.cost_usd is not None]
    )


def test_cost_source_is_reported_when_the_harness_priced_everything():
    doc = chat_session()
    doc["steps"][2]["metrics"]["cost_usd"] = 0.25
    trajectory = validate_trajectory(doc)
    reported = priced_steps(trajectory)
    assert digest("s", trajectory, reported).cost_source == "reported"


def test_cost_source_is_estimated_when_evalkit_priced_everything():
    doc = chat_session()
    del doc["steps"][1]["metrics"]["cost_usd"]
    trajectory = validate_trajectory(doc)
    reported = priced_steps(trajectory)
    backfill_step_costs(trajectory, COST_MAP)
    assert digest("s", trajectory, reported).cost_source == "estimated"


def test_percentile_is_nearest_rank():
    assert percentile([], 0.5) is None
    assert percentile([5.0, 1.0, 3.0], 0.5) == 3.0
    assert percentile([float(n) for n in range(1, 11)], 0.9) == 9.0


def test_stats_group_by_day_in_the_requested_timezone():
    trajectory, reported = priced()
    one = digest("a", trajectory, reported)
    item = StatsInput(
        digest=one,
        turn_durations=[30.0, 100.0],
        source="app",
        user_id="u-1",
        created_at=datetime(2026, 10, 20, 23, 0, tzinfo=UTC),
    )
    other = item.model_copy(update={"source": "eval", "user_id": None})
    (total,) = group_stats([item, other], ZoneInfo("UTC"), "none")
    assert total.group == {}
    assert (total.runs, total.runs_with_failure, total.turns, total.failed_turns) == (2, 2, 4, 2)
    assert total.turn_p50_seconds == 30.0 and total.turn_p90_seconds == 100.0
    assert total.runs_with_estimated_cost == 2 and total.tool_errors == 6
    assert [r.group for r in group_stats([item], ZoneInfo("UTC"), "day")] == [{"day": "2026-10-20"}]
    berlin = group_stats([item], ZoneInfo("Europe/Berlin"), "day")
    assert [r.group for r in berlin] == [{"day": "2026-10-21"}]
    tools = group_stats([item, other], ZoneInfo("UTC"), "tool")
    assert [(r.group["tool"], r.tool_errors, r.runs) for r in tools] == [
        ("search_docs", 4, 2),
        ("Bash", 2, 2),
    ]
    assert tools[0].sample_error == "unknown_column: name"
    by_source = group_stats([item, other], ZoneInfo("UTC"), "source")
    assert sorted((r.group["source"], r.runs) for r in by_source) == [("app", 1), ("eval", 1)]
    by_user = group_stats([item, other], ZoneInfo("UTC"), "user")
    assert sorted(r.group["user"] for r in by_user) == ["u-1", "unknown"]
