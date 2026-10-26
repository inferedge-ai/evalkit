import httpx
import pytest

from evalkit.cli.local_run import run_local
from evalkit.client.connection import Connection
from evalkit.config import CostMapConfig, LocalConfig
from evalkit.contracts.enums import ExperimentStatus
from evalkit.core.workspace import Reference
from evalkit.credentials import CredentialsConfig
from evalkit.store.base import GitRevision, Selection
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent, seed_evaluators
from tests.native import custom_eval_app, seed_env, serve


@pytest.fixture
async def ctx(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(workspace_files={"outputs/report.md": b"# report"})
    env = await seed_env(session_factory, tmp_path, fake)
    await seed_agent(session_factory, "fake", "admin")
    await seed_evaluators(session_factory, "admin")
    async with serve(env, tmp_path) as client:
        yield client, env, fake


def local_config(tmp_path) -> LocalConfig:
    cost_map = tmp_path / "cost_map.json"
    cost_map.write_text("{}")
    (tmp_path / ".env").write_text(
        "LOCAL_AGENT_KEY=client-key\nLOCAL_EVAL_KEY=k\nLOCAL_JUDGE_KEY=jk\n"
    )
    return LocalConfig(
        path=tmp_path / "evalkit.yaml",
        cases=tmp_path / "cases",
        fixtures=tmp_path / "fixtures",
        cost_map=CostMapConfig(source=str(cost_map)),
        credentials=CredentialsConfig(
            agents={"fake": "env:LOCAL_AGENT_KEY"},
            evaluators={"required_tool_calls": "env:LOCAL_EVAL_KEY"},
            judges={"http://judge.test": "env:LOCAL_JUDGE_KEY"},
        ),
    )


async def test_local_run_writes_everything_to_the_server(ctx, tmp_path):
    client, env, fake = ctx
    token = client.headers["Authorization"].removeprefix("Bearer ")
    conn = Connection("http://test", token, "admin")
    lines: list[str] = []
    exp_id = await run_local(
        conn,
        local_config(tmp_path),
        agent=Reference("fake", "admin"),
        dataset_slug="finance@canonical",
        selection=Selection(all=True),
        repetitions=1,
        model=None,
        include_drafts=False,
        resume=None,
        revision=GitRevision(sha="abc", branch="feat", dirty=True),
        progress=lines.append,
        transport=httpx.ASGITransport(app=fake.app),
        custom_eval_transport=httpx.ASGITransport(app=custom_eval_app()),
        api_transport=client._transport,
    )
    exp = await env.experiment(exp_id)
    assert exp.status is ExperimentStatus.COMPLETED, exp.status_error
    assert exp.agent == "fake@admin"
    assert exp.created_by == "admin"
    assert exp.workspace == "admin"
    assert exp.config.revision == GitRevision(sha="abc", branch="feat", dirty=True)
    assert exp.config.snapshot.evalkit_version
    assert exp.config.snapshot.trajectory_source == "atif"
    assert exp.config.snapshot.trajectory_wait_seconds == 60
    assert exp.lease_holder is None
    runs = await env.details(exp_id)
    assert len(runs) == 2
    for run in runs:
        record = run.agent_run
        assert record.trajectory_key and env.blob.objects[record.trajectory_key]
        assert (
            env.blob.objects[f"artifacts/{exp.agent_id}/{record.harness_run_id}/outputs/report.md"]
            == b"# report"
        )
        names = {e.name for e in run.evaluations}
        assert {"answer_rubric_score", "required_tool_calls", "gauge_duration_seconds"} <= names
    listed = (await client.get("/api/agents/fake@admin/runs")).json()
    assert listed["total"] == 2
    assert {r["experiment_id"] for r in listed["runs"]} == {exp_id}
    assert any(line.startswith("run case-1 #1 completed") for line in lines)
    assert any(line.startswith("scored case-1 #1") for line in lines)
    assert lines[0].startswith(f"experiment {exp_id} ")


async def test_resume_runs_only_pending_cases(ctx, tmp_path):
    client, env, fake = ctx
    token = client.headers["Authorization"].removeprefix("Bearer ")
    conn = Connection("http://test", token, "admin")
    kwargs = dict(
        agent=Reference("fake", "admin"),
        dataset_slug="finance@canonical",
        selection=Selection(all=True),
        repetitions=1,
        model=None,
        include_drafts=False,
        revision=GitRevision(),
        progress=lambda line: None,
        transport=httpx.ASGITransport(app=fake.app),
        custom_eval_transport=httpx.ASGITransport(app=custom_eval_app()),
        api_transport=client._transport,
    )
    exp_id = await run_local(conn, local_config(tmp_path), resume=None, **kwargs)
    before = {r.agent_run_id for r in await env.details(exp_id)}
    await env.store.transition(exp_id, ExperimentStatus.EVALUATING, "pod:test")
    await env.store.transition(exp_id, ExperimentStatus.FAILED, "pod:test", error="x")
    assert await run_local(conn, local_config(tmp_path), resume=exp_id, **kwargs) == exp_id
    after = {r.agent_run_id for r in await env.details(exp_id)}
    assert after == before
    assert (await env.experiment(exp_id)).status is ExperimentStatus.COMPLETED
