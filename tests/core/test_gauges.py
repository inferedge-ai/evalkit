import json
from pathlib import Path

from evalkit.contracts.atif import validate_trajectory
from evalkit.contracts.harness import Capabilities
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.evaluators.base import BUILTIN_GAUGES, EvalContext, execute
from evalkit.core.judge import JudgeUsage
from evalkit.core.trajectory import TrajectoryView, derive_metrics
from evalkit.store.base import CaseView

DATA = Path(__file__).parent.parent / "data"
JUDGE = JudgeSettings(base_url="http://j", model="judge-model")
COST_MAP = {"judge-model": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}}

FULL_CAPS = Capabilities.model_validate(
    {
        "agent": "x",
        "provides": {"trajectory": True, "tool_calls": True, "token_usage": True},
    }
)
NO_CAPS = Capabilities.model_validate({"agent": "x"})


def make_ctx(with_view: bool = True) -> EvalContext:
    view = None
    metrics = None
    if with_view:
        doc = validate_trajectory(json.loads((DATA / "atif_full.json").read_text()))
        view = TrajectoryView(doc)
        metrics = derive_metrics(view, 5000)
    case = CaseView(case_ref=1, case_id="c1", content_hash="h", input={})
    return EvalContext(
        case=case,
        answer="a",
        trajectory_view=view,
        wall_ms=5000,
        judge_cfg=JUDGE,
        metrics=metrics,
        cost_map=COST_MAP,
    )


async def run_gauge(name: str, ctx: EvalContext, caps: Capabilities):
    return (await execute(BUILTIN_GAUGES[name], ctx, {}, caps))[0]


async def test_all_gauges_read(monkeypatch):
    ctx = make_ctx()
    ctx.record_judge_usage(JudgeUsage(input_tokens=1000, output_tokens=100))
    expectations = {
        "gauge_duration_seconds": 5.0,
        "gauge_input_tokens": 3200,
        "gauge_output_tokens": 450,
        "gauge_cached_tokens": 2300,
        "gauge_agent_cost_usd": 0.02,
        "gauge_judge_cost_usd": round(1000 * 1e-6 + 100 * 2e-6, 4),
        "gauge_total_tool_calls": 3,
        "gauge_errored_tool_calls": 1,
        "gauge_subagents": 2,
        "gauge_turns": 2,
    }
    for name, expected in expectations.items():
        row = await run_gauge(name, ctx, FULL_CAPS)
        assert row.score == expected, f"{name}: {row.score} != {expected}"
        assert row.kind == "gauge" and row.label == "info"


async def test_gauges_skip_without_capability():
    ctx = make_ctx()
    for name in ["gauge_input_tokens", "gauge_total_tool_calls", "gauge_subagents"]:
        row = await run_gauge(name, ctx, NO_CAPS)
        assert row.score is None


async def test_gauges_no_reading_without_input():
    ctx = make_ctx(with_view=False)
    for name in ["gauge_input_tokens", "gauge_total_tool_calls", "gauge_judge_cost_usd"]:
        row = await run_gauge(name, ctx, FULL_CAPS)
        assert row.score is None
        assert row.explanation == "no reading"
