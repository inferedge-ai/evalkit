import json
from pathlib import Path

import pytest

from evalkit.contracts.atif import validate_trajectory
from evalkit.core.trajectory import TrajectoryView, backfill_step_costs, derive_metrics

DATA = Path(__file__).parent.parent / "data"


@pytest.fixture
def view() -> TrajectoryView:
    doc = validate_trajectory(json.loads((DATA / "atif_full.json").read_text()))
    return TrajectoryView(doc)


def test_tool_calls_flatten_subagents(view):
    names = [c.function_name for c in view.tool_calls()]
    assert names == ["web_search", "read_file", "fetch_url"]


def test_errored_count(view):
    assert view.errored_tool_call_count() == 1


def test_token_totals_from_final_metrics(view):
    totals = view.token_totals()
    assert (totals.input, totals.output, totals.cached) == (3200, 450, 2300)


def test_token_totals_summed_without_final_metrics(view):
    view.trajectory.final_metrics = None
    totals = view.token_totals()
    assert totals.input == 3200 + 500
    assert totals.output == 450 + 80
    assert totals.cached == 2300


def test_counts(view):
    assert view.subagent_count() == 2
    assert view.turn_count() == 2


def test_render(view):
    rendered = view.render()
    assert "Recorded steps (in order):" in rendered
    assert "1. " in rendered
    assert "web_search" in rendered
    assert "[ERROR]" in rendered
    assert "ENOENT" in rendered


MINI_MAP = {"gpt-5.2-mini": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}


def test_derive_metrics_harness_total_wins(view):
    metrics = derive_metrics(view, wall_ms=1234)
    assert metrics.cost_usd == 0.02  # final_metrics.total_cost_usd
    assert metrics.duration_ms == 1234
    assert metrics.total_tool_calls == 3
    assert metrics.errored_tool_calls == 1
    assert metrics.subagents == 2
    assert metrics.turns == 2


def test_derive_metrics_sums_priced_steps(view):
    view.trajectory.final_metrics.total_cost_usd = None
    metrics = derive_metrics(view, wall_ms=1)
    assert metrics.cost_usd == 0.02  # main steps 0.012 + 0.008


def test_derive_metrics_none_without_costs(view):
    view.trajectory.final_metrics.total_cost_usd = None
    for step in view.trajectory.steps:
        step.metrics = None
    for sub in view.trajectory.subagent_trajectories or []:
        for step in sub.steps:
            step.metrics = None
    metrics = derive_metrics(view, wall_ms=1)
    assert metrics.cost_usd is None


def test_backfill_then_derive_sums_all_priced_steps(view):
    # The unification: after backfill prices the uncosted subagent step, the
    # gauge sum includes it alongside the harness-priced main steps.
    view.trajectory.final_metrics.total_cost_usd = None
    backfill_step_costs(view.trajectory, MINI_MAP)
    metrics = derive_metrics(view, wall_ms=1)
    assert metrics.cost_usd == round(0.012 + 0.008 + (500 * 1e-6 + 80 * 2e-6), 10)


def test_backfill_prices_step_missing_cost_from_its_model(view):
    sub_step = view.trajectory.subagent_trajectories[0].steps[-1]
    assert sub_step.metrics.cost_usd is None
    backfill_step_costs(view.trajectory, MINI_MAP)
    assert sub_step.metrics.cost_usd == round(500 * 1e-6 + 80 * 2e-6, 10)


def test_backfill_uses_model_override(view):
    sub = view.trajectory.subagent_trajectories[0]
    sub.agent = None
    for step in sub.steps:
        step.model_name = None
    target = sub.steps[-1]
    backfill_step_costs(view.trajectory, MINI_MAP, model="gpt-5.2-mini")
    assert target.metrics.cost_usd == round(500 * 1e-6 + 80 * 2e-6, 10)


def test_backfill_leaves_priced_steps_untouched(view):
    priced = [s for s in view.trajectory.steps if s.metrics and s.metrics.cost_usd is not None]
    before = [s.metrics.cost_usd for s in priced]
    backfill_step_costs(view.trajectory, MINI_MAP)
    assert [s.metrics.cost_usd for s in priced] == before == [0.012, 0.008]


def test_backfill_prices_step_reporting_zero_cost_with_tokens(view):
    sub_step = view.trajectory.subagent_trajectories[0].steps[-1]
    sub_step.metrics.cost_usd = 0.0
    backfill_step_costs(view.trajectory, MINI_MAP)
    assert sub_step.metrics.cost_usd == round(500 * 1e-6 + 80 * 2e-6, 10)


def test_zero_harness_costs_are_repriced_end_to_end(view):
    view.trajectory.final_metrics.total_cost_usd = 0.0
    for step in view._all_steps():
        if step.metrics is not None:
            step.metrics.cost_usd = 0.0
    cost_map = {
        **MINI_MAP,
        "gpt-5.2": {"input_cost_per_token": 2e-6, "output_cost_per_token": 4e-6},
    }
    backfill_step_costs(view.trajectory, cost_map)
    metrics = derive_metrics(view, wall_ms=1)
    main_step_2 = 400 * 2e-6 + 300 * 4e-6 + 800 * 2e-6
    main_step_3 = 500 * 2e-6 + 150 * 4e-6 + 1500 * 2e-6
    sub_step = 500 * 1e-6 + 80 * 2e-6
    assert metrics.cost_usd == pytest.approx(main_step_2 + main_step_3 + sub_step)


def test_backfill_noop_when_model_unpriced(view):
    sub_step = view.trajectory.subagent_trajectories[0].steps[-1]
    backfill_step_costs(view.trajectory, {})
    assert sub_step.metrics.cost_usd is None


def _long_result_trajectory() -> TrajectoryView:
    big = "revenue 3,522,776 " * 40
    doc = validate_trajectory(
        {
            "schema_version": "ATIF-v1.8",
            "steps": [
                {
                    "step_id": 1,
                    "source": "agent",
                    "message": "Looking up revenue.",
                    "reasoning_content": "Need FY2024 platform revenue first.",
                    "tool_calls": [
                        {
                            "tool_call_id": "c1",
                            "function_name": "fetch",
                            "arguments": {"q": "roku 10-K " * 30},
                        }
                    ],
                    "observation": {
                        "results": [{"source_call_id": "c1", "content": big}],
                    },
                },
            ],
        }
    )
    return TrajectoryView(doc)


def test_render_steps_keeps_everything():
    view = _long_result_trajectory()
    lines = view.render_steps()
    assert all(line.startswith(f"{i}. ") for i, line in enumerate(lines, 1))
    joined = "\n".join(lines)
    assert "[reasoning] Need FY2024 platform revenue first." in joined
    assert joined.count("3,522,776") == 40
    assert joined.count("roku 10-K") == 30


def test_by_trajectory_splits_main_and_each_subagent(view):
    split = derive_metrics(view, wall_ms=1).by_trajectory
    assert list(split) == ["main", "traj-sub-research", "traj-sub-writer"]
    main = split["main"]
    assert main.agent == "myagent"
    assert (main.input_tokens, main.output_tokens, main.cached_tokens) == (3200, 450, 2300)
    assert main.cost_usd == pytest.approx(0.02)
    assert (main.total_tool_calls, main.errored_tool_calls, main.turns) == (2, 1, 2)
    research = split["traj-sub-research"]
    assert research.agent == "researcher"
    assert (research.input_tokens, research.output_tokens) == (500, 80)
    assert research.cached_tokens is None
    assert research.cost_usd is None
    assert (research.total_tool_calls, research.turns) == (1, 1)
    writer = split["traj-sub-writer"]
    assert (writer.agent, writer.input_tokens, writer.total_tool_calls) == ("writer", None, 0)


def _sub(name: str | None, prompt: int) -> dict:
    agent = {"name": name} if name else None
    return {
        "schema_version": "ATIF-v1.8",
        "agent": agent,
        "steps": [
            {
                "step_id": 1,
                "source": "agent",
                "message": "done",
                "metrics": {"prompt_tokens": prompt, "completion_tokens": 1, "cost_usd": 0.5},
            }
        ],
    }


def test_by_trajectory_never_merges_subagents_of_one_type():
    doc = {
        "schema_version": "ATIF-v1.8",
        "steps": [{"step_id": 1, "source": "user", "message": "go"}],
        "subagent_trajectories": [_sub("researcher", 10), _sub("researcher", 5), _sub(None, 7)],
    }
    split = TrajectoryView(validate_trajectory(doc)).metrics_by_trajectory()
    assert list(split) == [
        "main",
        "subagent_trajectories[0]",
        "subagent_trajectories[1]",
        "subagent_trajectories[2]",
    ]
    assert [m.agent for m in split.values()] == ["main", "researcher", "researcher", "subagent"]
    assert split["subagent_trajectories[0]"].input_tokens == 10
    assert split["subagent_trajectories[1]"].input_tokens == 5
    assert split["subagent_trajectories[1]"].cost_usd == pytest.approx(0.5)
    assert split["main"].input_tokens is None
    assert split["main"].turns == 0
