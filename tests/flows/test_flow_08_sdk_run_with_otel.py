import httpx
import sqlalchemy as sa
from typer.testing import CliRunner

from evalkit.cli.main import app as cli_app
from evalkit.db import models
from evalkit.sdk import RunResult, aevaluate_experiment, arun_experiment, evaluator
from tests.cli.test_cli import stub_judges_module
from tests.fake_harness import DEFAULT_TRAJECTORY
from tests.sdk.test_run_experiment import LLM_ATTRS, env, server

__all__ = ["env", "server", "stub_judges_module"]

STAMPED = {"session.id", "evalkit.case_id", "evalkit.repetition", "evalkit.experiment_id"}


@evaluator(name="required_tool_calls")
def cite(case, run):
    return 1.0 if run.answer == "42" else 0.0


async def sdk_spans(srv, exp_id: int) -> list[dict]:
    async def work(_, factory):
        async with factory() as session:
            rows = await session.scalars(
                sa.select(models.Span.attributes)
                .join(models.Agent, models.Agent.id == models.Span.agent_id)
                .where(models.Agent.name == "sdk")
            )
            return [a for a in rows if a.get("evalkit.experiment_id") == exp_id]

    return await srv._with_store(work)


def runs_browser(srv) -> list[dict]:
    token = httpx.post(
        f"{srv.base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    ).json()["token"]
    resp = httpx.get(
        f"{srv.base_url}/api/agents/sdk@admin/runs",
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.json()["runs"]


async def test_flow_08_sdk_run_stamps_spans_scores_and_lists_sessions(env):
    srv, blob, provider = env
    tracer = provider.get_tracer("client")

    def task(input: dict) -> str:
        with tracer.start_as_current_span("llm", attributes=LLM_ATTRS):
            pass
        return "42"

    lines: list[str] = []
    result = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="sdk",
        task=task,
        evaluators=[cite],
        model="gpt-x",
        tracer_provider=provider,
        poll_interval=0.2,
        progress=lines.append,
    )
    assert any("model=gpt-x is recorded on the experiment" in line for line in lines)
    exp_id = result.experiment_id
    assert isinstance(exp_id, int) and result.url.endswith(f"/experiments/{exp_id}")

    stamped = [a for a in await sdk_spans(srv, exp_id) if STAMPED <= set(a)]
    assert {(a["evalkit.case_id"], a["evalkit.repetition"]) for a in stamped} == {
        ("case-1", 1),
        ("case-2", 1),
    }
    sessions = {r.session_id for r in result.runs}
    assert len(sessions) == len(result.runs) == 2
    assert {a["session.id"] for a in stamped} == sessions

    exp, runs = await srv.experiment(exp_id)
    assert exp.status == "completed" and exp.lease_holder is None
    assert exp.workspace == "admin" and exp.dataset == "finance@canonical"
    assert exp.agent == "sdk@admin" and exp.config.model == "gpt-x"
    assert exp.config.snapshot.trajectory_source == "otel"
    for run in runs:
        assert run.agent_run.trajectory_status == "valid"
        by_name = {e.name: e for e in run.evaluations}
        assert by_name["required_tool_calls"].source == "code"
        assert by_name["required_tool_calls"].kind == "score"
        assert by_name["required_tool_calls"].value == 1.0

    listed = {r["run_id"] for r in runs_browser(srv)}
    assert sessions <= listed


async def test_flow_08_dry_run_resume_and_re_evaluate(env):
    srv, blob, provider = env
    before = await srv.count()
    dry = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=lambda q: RunResult(answer="42", trajectory=DEFAULT_TRAJECTORY),
        case_ids=["case-2"],
        dry_run=True,
        tracer_provider=provider,
        progress=lambda line: None,
    )
    assert dry.experiment_id is None and await srv.count() == before
    assert [r.case_id for r in dry.runs] == ["case-2"]

    def flaky(input: dict) -> RunResult:
        if input["question"] == "q2":
            raise RuntimeError("task blew up")
        return RunResult(answer="42", trajectory=DEFAULT_TRAJECTORY)

    first = await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=flaky,
        evaluators=[cite],
        trajectory_wait_seconds=0,
        tracer_provider=provider,
        progress=lambda line: None,
    )
    exp_id = first.experiment_id
    _, runs = await srv.experiment(exp_id)
    failed = next(r for r in runs if r.case_id == "case-2")
    assert failed.agent_run.status == "failed" and "task blew up" in failed.agent_run.error
    await srv.execute(
        sa.update(models.Experiment)
        .where(models.Experiment.id == exp_id)
        .values(status="failed", status_error="script died", lease_holder=None)
    )

    asked: list[str] = []

    def steady(input: dict) -> RunResult:
        asked.append(input["question"])
        return RunResult(answer="42", trajectory=DEFAULT_TRAJECTORY)

    await arun_experiment(
        dataset="finance",
        workspace="canonical",
        agent="pushed",
        task=steady,
        evaluators=[cite],
        resume=exp_id,
        tracer_provider=provider,
        progress=lambda line: None,
    )
    assert asked == ["q2"]
    exp, runs = await srv.experiment(exp_id)
    assert exp.status == "completed" and exp.status_error is None
    assert all(r.agent_run.status == "completed" for r in runs)

    async def run_errors(store, _):
        return (await store.aggregate(exp_id)).run_errors

    assert await srv._with_store(run_errors) == []

    await srv.execute(
        sa.delete(models.Evaluation).where(
            models.Evaluation.experiment_run_id.in_([r.experiment_run_id for r in runs]),
            models.Evaluation.name == "required_tool_calls",
        )
    )
    await aevaluate_experiment(experiment_id=exp_id, evaluators=[cite], progress=lambda line: None)
    _, runs = await srv.experiment(exp_id)
    for run in runs:
        assert {e.name: e.source for e in run.evaluations}["required_tool_calls"] == "code"

    refused = CliRunner().invoke(cli_app, ["evaluate", str(exp_id)])
    assert refused.exit_code == 1
    assert "required_tool_calls" in refused.output and "evaluate_experiment" in refused.output
