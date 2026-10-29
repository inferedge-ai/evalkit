import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import delete, update

from evalkit.contracts.cases import Case
from evalkit.contracts.enums import (
    DatasetKind,
    EvaluationSource,
    EvaluatorKind,
    ExperimentStatus,
    RunStatus,
    SyncSource,
    TrajectorySource,
)
from evalkit.contracts.harness import Capabilities
from evalkit.contracts.settings import JudgeSettings, RemoteEvaluatorSettings
from evalkit.core.evaluators import presentation, rubrics, trajectory_rubrics
from evalkit.core.evaluators.base import BUILTIN_EVALUATORS
from evalkit.core.evaluators.rubrics import ANSWER_RUBRIC_EMITS
from evalkit.core.harness import HarnessClient
from evalkit.core.judge import JudgeUsage
from evalkit.core.python_eval import PythonEvaluator
from evalkit.core.remote_evaluators import RemoteEvaluator
from evalkit.core.runner import (
    ExperimentRequest,
    InvalidCaseInputs,
    LeaseLost,
    ScoringFailed,
    drive_experiment,
    evaluate_experiment,
    rescore_experiment,
    run_experiment,
)
from evalkit.core.scorecard import assemble_report
from evalkit.core.traces.sessions import SessionTrajectory
from evalkit.credentials import CredentialRegistry, MissingCredential
from evalkit.db.models import AgentRun, Evaluation
from evalkit.db.models import Experiment as ExperimentRow
from evalkit.db.workspaces import WorkspaceStore
from evalkit.store.base import AgentRunRecord, IncomingOverlay, Selection
from tests.fake_agents import (
    FAKE_SETTINGS,
    JUDGE_URL,
    TEST_CREDENTIALS_CONFIG,
    TEST_ENV,
    FakeEvaluators,
    reference_mapping,
)
from tests.fake_harness import DEFAULT_TRAJECTORY, FakeHarness
from tests.identity import seed_agent
from tests.native import (
    CASE_EVALUATORS,
    FILE_BYTES,
    HOLDER,
    RECEIVED_EVAL_QUESTIONS,
    RECEIVED_EVAL_RUNS,
    approve,
    prepare,
    put_fixture,
    seed_env,
    start,
    suite_case,
    sync_cases,
)
from tests.seed import count

ATIF_DOC = json.loads((Path(__file__).parent.parent / "data" / "atif_full.json").read_text())

COMPLETED, FAILED = ExperimentStatus.COMPLETED, ExperimentStatus.FAILED


def scores_of(run) -> dict:
    return {e.name: e for e in run.evaluations}


async def clear_evaluations(env) -> None:
    async with env.session_factory() as s:
        await s.execute(delete(Evaluation))
        await s.commit()


async def traced_agent(env, **settings) -> None:
    await seed_agent(
        env.session_factory,
        "traced",
        trajectory_source=TrajectorySource.OTEL,
        settings=FAKE_SETTINGS.model_copy(update=settings),
        mapping=reference_mapping(),
    )
    agents = {**TEST_CREDENTIALS_CONFIG.agents, "traced@canonical": "env:FAKE_AGENT_KEY"}
    env.deps.credentials = CredentialRegistry(
        TEST_CREDENTIALS_CONFIG.model_copy(update={"agents": agents}), TEST_ENV
    )


async def test_end_to_end(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(answer="the agent answer")
    env = await seed_env(session_factory, tmp_path, fake, broken_on_case_2=True)
    exp = await start(
        env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical", repetitions=2)
    )
    assert exp.status is COMPLETED, exp.status_error
    assert exp.lease_holder is None
    runs = await env.details(exp.id)
    assert len(runs) == 4
    for run in runs:
        key = run.agent_run.trajectory_key
        assert key and await env.blob.object_exists(key)
        by_name = scores_of(run)
        assert "answer_rubric_score" in by_name
        assert "gauge_duration_seconds" in by_name
        assert "required_tool_calls" in by_name
        assert "max_response_time" in by_name
        assert by_name["answer_rubric_score"].value == 0.5
        assert by_name["answer_rubric_score"].producer == "answer_rubrics"
        assert by_name["required_tool_calls"].source is EvaluationSource.REMOTE
        assert by_name["gauge_judge_cost_usd"].value is None or isinstance(
            by_name["gauge_judge_cost_usd"].value, float
        )
        if run.case_id == "case-2":
            assert by_name["broken_eval"].error is not None
            assert by_name["broken_eval"].label == "error"


async def test_resume_finishes_evaluate_only(tmp_path, stub_judges, session_factory, monkeypatch):
    env = await seed_env(session_factory, tmp_path)

    async def dying_evaluate(deps_, exp_id, holder=None):
        raise RuntimeError("killed after phase 1")

    monkeypatch.setattr("evalkit.core.runner.evaluate_experiment", dying_evaluate)
    exp = await prepare(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    with pytest.raises(RuntimeError):
        await run_experiment(env.deps, exp.id, HOLDER)
    assert (await env.experiment(exp.id)).status is FAILED
    before = await env.details(exp.id)
    assert len(before) == 2

    monkeypatch.setattr("evalkit.core.runner.evaluate_experiment", evaluate_experiment)
    await env.store.transition(exp.id, ExperimentStatus.RUNNING, HOLDER)
    await run_experiment(env.deps, exp.id, HOLDER)
    after = await env.details(exp.id)
    assert [r.agent_run_id for r in after] == [r.agent_run_id for r in before]
    assert (await env.experiment(exp.id)).status is COMPLETED
    assert all(run.evaluations for run in after)


async def test_interrupt_then_resume_runs_only_pending_cases(
    tmp_path, stub_judges, session_factory
):
    fake = FakeHarness()
    env = await seed_env(session_factory, tmp_path, fake)
    exp = await prepare(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    await env.store.record_run(
        exp.id,
        "case-1",
        1,
        AgentRunRecord(
            harness_run_id="done-before",
            status=RunStatus.COMPLETED,
            answer="42",
            started_at=exp.started_at,
        ),
    )
    await env.store.transition(exp.id, FAILED, HOLDER, error="interrupted: cancelled by admin")
    await env.store.transition(exp.id, ExperimentStatus.RUNNING, "cli:admin@laptop:1")
    await run_experiment(env.deps, exp.id, "cli:admin@laptop:1")
    assert [p["input"]["question"] for p in fake.received_payloads.values()] == ["q2"]
    final = await env.experiment(exp.id)
    assert final.status is COMPLETED and final.status_error is None
    assert {r.case_id for r in await env.details(exp.id)} == {"case-1", "case-2"}


async def test_latest_attempt_wins_in_the_scorecard(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    fake = FakeHarness(fail_run=True)
    env = await seed_env(session_factory, tmp_path, fake)

    async def dying_evaluate(deps_, exp_id, holder=None):
        raise RuntimeError("stop")

    monkeypatch.setattr("evalkit.core.runner.evaluate_experiment", dying_evaluate)
    exp = await prepare(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    with pytest.raises(RuntimeError):
        await run_experiment(env.deps, exp.id, HOLDER)
    monkeypatch.setattr("evalkit.core.runner.evaluate_experiment", evaluate_experiment)
    await evaluate_experiment(env.deps, exp.id)
    failed = assemble_report(await env.store.aggregate(exp.id))
    assert all(case.run_errors == ["agent crashed"] for case in failed.cases)

    fake.fail_run = False
    await env.store.transition(exp.id, ExperimentStatus.RUNNING, HOLDER)
    await run_experiment(env.deps, exp.id, HOLDER)
    report = assemble_report(await env.store.aggregate(exp.id))
    assert all(case.run_errors == [] for case in report.cases)
    assert report.buckets["content"] == 0.5
    assert await count(session_factory, AgentRun) == 4


async def test_a_harness_that_reuses_a_run_id_fails_only_that_run(
    tmp_path, stub_judges, session_factory
):
    fake = FakeHarness()
    env = await seed_env(session_factory, tmp_path, fake)
    first = await prepare(
        env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical")
    )
    await run_experiment(env.deps, first.id, HOLDER)
    kept = {
        r.agent_run.harness_run_id: r.agent_run.trajectory_key for r in await env.details(first.id)
    }
    trajectories = {key: env.blob.objects[key] for key in kept.values()}

    fake._counter = 0
    second = await prepare(
        env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical")
    )
    await run_experiment(env.deps, second.id, HOLDER)

    assert (await env.experiment(second.id)).status is COMPLETED
    runs = [r.agent_run for r in await env.details(second.id)]
    assert runs and all(run.status == "failed" for run in runs)
    assert all(run.error.startswith("run or session id sess-") for run in runs)
    assert all("#reused-" in run.harness_run_id and run.trajectory_key is None for run in runs)
    assert {key: env.blob.objects[key] for key in kept.values()} == trajectories


async def test_lost_lease_stops_the_drive(tmp_path, stub_judges, session_factory, monkeypatch):
    env = await seed_env(session_factory, tmp_path, FakeHarness(complete_after_polls=10**9))
    exp = await prepare(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    monkeypatch.setattr("evalkit.core.runner.RENEW_EVERY_SECONDS", 0.01)

    async def lost(exp_id: int, holder: str) -> bool:
        return False

    monkeypatch.setattr(env.store, "renew_lease", lost)
    with pytest.raises(LeaseLost):
        await run_experiment(env.deps, exp.id, HOLDER)
    after = await env.experiment(exp.id)
    assert after.status is ExperimentStatus.RUNNING
    assert after.lease_holder == HOLDER
    assert await env.details(exp.id) == []


async def test_a_failed_renew_is_retried_until_the_lease_would_expire(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    env = await seed_env(session_factory, tmp_path, FakeHarness(complete_after_polls=3))
    exp = await prepare(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    monkeypatch.setattr("evalkit.core.runner.RENEW_EVERY_SECONDS", 0.001)
    real_renew = env.store.renew_lease
    calls: list[int] = []

    async def flaky(exp_id: int, holder: str) -> bool:
        calls.append(exp_id)
        if len(calls) % 2:
            raise ConnectionError("502 Bad Gateway")
        return await real_renew(exp_id, holder)

    monkeypatch.setattr(env.store, "renew_lease", flaky)
    await run_experiment(env.deps, exp.id, HOLDER)
    assert (await env.experiment(exp.id)).status is COMPLETED

    monkeypatch.setattr("evalkit.core.runner.LEASE_SECONDS", 0)

    async def down(exp_id: int, holder: str) -> bool:
        raise ConnectionError("server unreachable")

    monkeypatch.setattr(env.store, "renew_lease", down)
    second = await prepare(
        env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical")
    )
    with pytest.raises(LeaseLost):
        await run_experiment(env.deps, second.id, HOLDER)


async def test_failed_run_recorded(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness(fail_run=True))
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert all(run.agent_run.error == "agent crashed" for run in await env.details(exp.id))
    assert exp.status is COMPLETED


async def test_harness_model_and_revision_recorded(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(model="vendor/big-1", capabilities_overrides={"revision": "deadbeef"})
    env = await seed_env(session_factory, tmp_path, fake)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert exp.config.model == "vendor/big-1"
    assert exp.config.snapshot.capabilities["revision"] == "deadbeef"


async def test_selection_by_case_id(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path)
    exp = await start(
        env,
        ExperimentRequest(
            dataset="finance@canonical", agent="fake@canonical", selection={"case_ids": ["case-1"]}
        ),
    )
    assert [run.case_id for run in await env.details(exp.id)] == ["case-1"]


def test_emitted_names_from_registry():
    assert set(BUILTIN_EVALUATORS["answer_rubrics"].emits) == set(ANSWER_RUBRIC_EMITS)
    assert set(BUILTIN_EVALUATORS["presentation_judge"].emits) == {"presentation_judge"}
    assert set(BUILTIN_EVALUATORS["trajectory_rubrics"].emits) == {"trajectory_rubric_score"}


async def seed_fixture_case(env, *, question: str = "Compute net debt.") -> str:
    file = await put_fixture(env)
    await sync_cases(
        env,
        [suite_case("case-fx", question, fixtures=["bs.csv"])],
        files={"bs.csv": file},
    )
    await approve(env, case_ids=["case-fx"])
    return file.sha256


async def recording(monkeypatch) -> list[str]:
    prompts: list[str] = []
    real_judge = rubrics.call_judge

    async def recording_judge(cfg, **kwargs):
        prompts.append(kwargs["user_prompt"])
        return await real_judge(cfg, **kwargs)

    monkeypatch.setattr(rubrics, "call_judge", recording_judge)
    return prompts


async def test_fixture_case_sends_file_with_context_and_raw_question(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    judge_prompts = await recording(monkeypatch)
    fake = FakeHarness()
    env = await seed_env(session_factory, tmp_path, fake)
    await seed_fixture_case(env)
    exp = await start(
        env,
        ExperimentRequest(
            dataset="finance@canonical", agent="fake@canonical", selection={"case_ids": ["case-fx"]}
        ),
    )
    run_id = next(iter(fake.received_payloads))
    assert fake.received_files[run_id] == [("bs.csv", FILE_BYTES)]
    payload = fake.received_payloads[run_id]
    assert payload["input"] == {"question": "Compute net debt."}
    assert payload["files"] == [{"name": "bs.csv", "context": "Balance sheet."}]
    assert [r.agent_run.status for r in await env.details(exp.id)] == [RunStatus.COMPLETED]
    assert judge_prompts
    assert all(
        "Attached files:\n- bs.csv: Balance sheet.\n\nCompute net debt." in prompt
        for prompt in judge_prompts
    )


async def test_legacy_composed_question_is_sent_as_stored(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    judge_prompts = await recording(monkeypatch)
    composed = "Attached files:\n- bs.csv: Balance sheet.\n\nCompute net debt."
    fake = FakeHarness()
    env = await seed_env(session_factory, tmp_path, fake)
    await seed_fixture_case(env, question=composed)
    await start(
        env,
        ExperimentRequest(
            dataset="finance@canonical", agent="fake@canonical", selection={"case_ids": ["case-fx"]}
        ),
    )
    run_id = next(iter(fake.received_payloads))
    assert fake.received_payloads[run_id]["input"]["question"] == composed
    assert all(prompt.count("Attached files:") == 1 for prompt in judge_prompts)


async def test_fixture_case_sends_urls_when_harness_accepts_them(
    tmp_path, stub_judges, session_factory
):
    fake = FakeHarness(capabilities_overrides={"accepts": {"file_urls": True}})
    env = await seed_env(session_factory, tmp_path, fake)
    digest = await seed_fixture_case(env)
    exp = await start(
        env,
        ExperimentRequest(
            dataset="finance@canonical", agent="fake@canonical", selection={"case_ids": ["case-fx"]}
        ),
    )
    run_id = next(iter(fake.received_payloads))
    assert fake.received_file_urls[run_id] == [
        {
            "name": "bs.csv",
            "url": f"memory://blob/fixtures/sha256/{digest}",
            "context": "Balance sheet.",
        }
    ]
    assert run_id not in fake.received_files
    assert "files" not in fake.received_payloads[run_id]["input"]
    assert [r.agent_run.status for r in await env.details(exp.id)] == [RunStatus.COMPLETED]


async def test_fixture_case_not_gated_when_only_urls_accepted(
    tmp_path, stub_judges, session_factory
):
    fake = FakeHarness(capabilities_overrides={"accepts": {"files": False, "file_urls": True}})
    env = await seed_env(session_factory, tmp_path, fake)
    await seed_fixture_case(env)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert {r.case_id for r in await env.details(exp.id)} == {"case-1", "case-2", "case-fx"}


async def test_fixture_case_gated_without_accepts_files(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(capabilities_overrides={"accepts": {"files": False}})
    env = await seed_env(session_factory, tmp_path, fake)
    await seed_fixture_case(env)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert {r.case_id for r in await env.details(exp.id)} == {"case-1", "case-2"}
    assert {c.case_id for c in await env.store.pinned_cases(exp.id)} == {"case-1", "case-2"}


async def test_shared_run_includes_every_review_state(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, approved=False)
    await approve(env, case_ids=["case-1"])
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert {run.case_id for run in await env.details(exp.id)} == {"case-1", "case-2"}
    assert [c.case_id for c in await env.store.pinned_cases(exp.id)] == ["case-1", "case-2"]
    assert exp.config.selection == Selection(all=True)


async def test_structured_output_stored_and_sent_to_evaluators(
    tmp_path, stub_judges, session_factory
):
    RECEIVED_EVAL_RUNS.clear()
    fake = FakeHarness(answer=None, output={"label": "EVENT", "confidence": 0.9})
    env = await seed_env(session_factory, tmp_path, fake)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    for run in await env.details(exp.id):
        assert run.agent_run.answer is None
        assert run.agent_run.output == {"label": "EVENT", "confidence": 0.9}
        assert "answer_rubric_score" in scores_of(run)
    assert RECEIVED_EVAL_RUNS[0]["output"] == {"label": "EVENT", "confidence": 0.9}
    assert RECEIVED_EVAL_RUNS[0]["answer"] == '{"confidence": 0.9, "label": "EVENT"}'


SOURCE_SCHEMA = {
    "type": "object",
    "required": ["question", "source_type"],
    "properties": {"question": {"type": "string"}, "source_type": {"type": "string"}},
}


async def test_invalid_case_inputs_refuse_the_experiment(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(capabilities_overrides={"input_schema": SOURCE_SCHEMA})
    env = await seed_env(session_factory, tmp_path, fake)
    with pytest.raises(InvalidCaseInputs) as exc:
        await prepare(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert exc.value.bad == {
        "case-1": "<root>: 'source_type' is a required property",
        "case-2": "<root>: 'source_type' is a required property",
    }
    assert "fake" in str(exc.value) and "case-1" in str(exc.value)
    assert await count(session_factory, ExperimentRow) == 0
    assert fake.runs == {}


async def test_valid_case_inputs_run(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(
        capabilities_overrides={"input_schema": {"type": "object", "required": ["question"]}}
    )
    env = await seed_env(session_factory, tmp_path, fake)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert len(await env.details(exp.id)) == 2


async def test_default_schema_refuses_cases_without_a_question(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path)
    await sync_cases(
        env, [Case.model_validate({"id": "t-1", "input": {"text": "doc"}})], dataset="typed"
    )
    with pytest.raises(InvalidCaseInputs) as exc:
        await prepare(
            env,
            ExperimentRequest(dataset="typed@canonical", agent="fake@canonical"),
        )
    assert exc.value.bad == {"t-1": "<root>: 'question' is a required property"}


async def test_gated_fixture_cases_are_not_checked(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(capabilities_overrides={"accepts": {"files": False, "file_urls": False}})
    env = await seed_env(session_factory, tmp_path, fake)
    file = await put_fixture(env, key="a.csv")
    await sync_cases(
        env,
        [Case.model_validate({"id": "fx-1", "input": {}, "fixtures": ["a.csv"]})],
        files={"a.csv": file},
    )
    await approve(env)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert {r.case_id for r in await env.details(exp.id)} == {"case-1", "case-2"}


@pytest.mark.parametrize(
    ("error", "expected"),
    [(httpx.ReadError(""), "ReadError"), (RuntimeError("boom"), "RuntimeError: boom")],
    ids=["empty-message", "with-message"],
)
async def test_failed_experiment_error_names_exception_type(
    tmp_path, stub_judges, session_factory, monkeypatch, error, expected
):
    env = await seed_env(session_factory, tmp_path)

    async def failing_evaluate(deps_, exp_id, holder=None):
        raise error

    monkeypatch.setattr("evalkit.core.runner.evaluate_experiment", failing_evaluate)
    exp = await prepare(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    with pytest.raises(type(error)):
        await run_experiment(env.deps, exp.id, HOLDER)
    after = await env.experiment(exp.id)
    assert after.status is FAILED
    assert after.status_error == expected
    assert after.lease_holder is None


async def test_artifacts_persisted_eagerly(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(workspace_files={"outputs/report.md": b"# report"})
    env = await seed_env(session_factory, tmp_path, fake)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    for run in await env.details(exp.id):
        key = f"artifacts/{exp.agent_id}/{run.agent_run.harness_run_id}/outputs/report.md"
        assert env.blob.objects[key] == b"# report"


async def test_prepare_records_workspace_cases_and_version(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path)
    caps = Capabilities.model_validate(FakeHarness().capabilities)
    from_req = ExperimentRequest(
        dataset="finance@canonical",
        agent="fake@akshat",
        capabilities=caps,
        cases={"sha": "abc", "branch": "feat", "dirty": True},
        evalkit_version="0.4.0",
    )
    exp = await prepare(env, from_req, runner="akshat")
    assert exp.agent == "fake@akshat" and exp.workspace == "akshat"
    assert exp.created_by == "akshat"
    assert exp.config.revision is not None
    assert exp.config.revision.model_dump() == {"sha": "abc", "branch": "feat", "dirty": True}
    assert exp.config.snapshot.evalkit_version == "0.4.0"
    assert exp.config.snapshot.capabilities["agent"] == "fake"


async def test_workspace_dataset_skips_review_pinning(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, approved=False)
    await sync_cases(
        env, [suite_case("case-1", "q1"), suite_case("case-2", "q2")], workspace="akshat"
    )
    exp = await start(env, ExperimentRequest(dataset="finance@akshat", agent="fake@canonical"))
    assert {run.case_id for run in await env.details(exp.id)} == {"case-1", "case-2"}


async def test_drive_uses_agent_name_part_of_slug(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path)
    caps = Capabilities.model_validate(FakeHarness().capabilities)
    exp = await prepare(
        env,
        ExperimentRequest(dataset="finance@canonical", agent="fake@akshat", capabilities=caps),
        runner="akshat",
    )
    await run_experiment(env.deps, exp.id, HOLDER)
    assert (await env.experiment(exp.id)).status is COMPLETED


async def test_progress_callback_sees_runs_and_scores(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path)
    lines: list[str] = []
    env.deps.progress = lines.append
    await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert any(line.startswith("run case-1") for line in lines)
    assert any(line.startswith("scored case-1") for line in lines)


async def test_unconfigured_custom_evaluator_records_an_error_row(
    tmp_path, stub_judges, session_factory
):
    env = await seed_env(session_factory, tmp_path)
    env.deps.evaluators = FakeEvaluators({})
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert exp.status is COMPLETED
    row = scores_of((await env.details(exp.id))[0])["required_tool_calls"]
    assert row.label == "error" and row.error == (
        "custom evaluator required_tool_calls is not configured"
    )


async def test_workspace_dataset_reads_fixture_bytes_by_sha(tmp_path, stub_judges, session_factory):
    fake = FakeHarness()
    env = await seed_env(session_factory, tmp_path, fake)
    file = await put_fixture(env)
    await sync_cases(
        env,
        [suite_case("case-fx", "Compute net debt.", evaluators={}, fixtures=["bs.csv"])],
        workspace="akshat",
        files={"bs.csv": file},
    )
    caps = Capabilities.model_validate(fake.capabilities)
    exp = await prepare(
        env,
        ExperimentRequest(dataset="finance@akshat", agent="fake@akshat", capabilities=caps),
        runner="akshat",
    )
    await run_experiment(env.deps, exp.id, HOLDER)
    run_id = next(iter(fake.received_payloads))
    assert fake.received_files[run_id] == [("bs.csv", FILE_BYTES)]
    assert (await env.experiment(exp.id)).status is COMPLETED


OVERLAY = IncomingOverlay(
    target_case_id="case-1",
    agents=["fake"],
    tags=[],
    evaluators={"custom": {"required_tool_calls": {"tools": ["x"]}}},
    source_file="overlays.yaml",
)


async def seed_overlaid(env) -> None:
    await sync_cases(
        env,
        [suite_case("case-1", "q1", evaluators={"output": CASE_EVALUATORS["output"]})],
        full=True,
    )
    await approve(env)
    ws = await WorkspaceStore(env.session_factory).get_by_name("canonical")
    await env.store.sync(
        workspace=ws,
        dataset="fake-cases",
        kind=DatasetKind.SUITE,
        incoming=[],
        overlays=[OVERLAY],
        full=True,
        actor_id=env.user_ids["admin"],
        source=SyncSource.GIT,
        detail={},
    )


@pytest.mark.parametrize(
    ("agent", "scored"), [("fake@canonical", True), ("other@canonical", False)]
)
async def test_overlay_scores_only_the_agents_it_names(
    tmp_path, stub_judges, session_factory, agent, scored
):
    env = await seed_env(session_factory, tmp_path, approved=False)
    await seed_agent(session_factory, "other")
    await seed_overlaid(env)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent=agent))
    names = {e.name for run in await env.details(exp.id) for e in run.evaluations}
    assert "answer_rubric_score" in names
    assert ("required_tool_calls" in names) is scored
    [pinned] = await env.store.pinned_cases(exp.id)
    assert ("custom" in pinned.effective_evaluators) is scored


async def test_python_evaluator_dispatch_and_requires_trajectory(
    tmp_path, stub_judges, session_factory
):
    seen: list[str] = []

    def rtc(case, run):
        seen.append(case.id)
        return 1.0

    fake = FakeHarness(capabilities_overrides={"provides": {"trajectory": False}})
    env = await seed_env(session_factory, tmp_path, fake)
    env.deps.evaluators = FakeEvaluators({})
    env.deps.python_evaluators["required_tool_calls"] = PythonEvaluator(
        name="required_tool_calls", kind="evaluator", fn=rtc, requires_trajectory=True
    )
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    rows = {e.name: e for run in await env.details(exp.id) for e in run.evaluations}
    assert seen == [] and rows["required_tool_calls"].label == "skip"
    env.deps.python_evaluators["required_tool_calls"] = PythonEvaluator(
        name="required_tool_calls", kind="evaluator", fn=rtc
    )
    await clear_evaluations(env)
    await evaluate_experiment(env.deps, exp.id)
    rows = {e.name: e for run in await env.details(exp.id) for e in run.evaluations}
    assert sorted(seen) == ["case-1", "case-2"] and rows["required_tool_calls"].value == 1.0
    assert rows["required_tool_calls"].source is EvaluationSource.CODE


class StubSessions:
    def __init__(self, responses):
        self.responses = responses

    async def trajectory(self, agent, session_id):
        return self.responses[session_id]


async def test_refetch_trajectories_rescored_from_blob(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(capabilities_overrides={"provides": {"trajectory": False}})
    env = await seed_env(session_factory, tmp_path, fake)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    first = (await env.details(exp.id))[0]
    async with session_factory() as s:
        await s.execute(
            update(AgentRun)
            .where(AgentRun.id == first.agent_run_id)
            .values(trajectory_status="unavailable")
        )
        await s.commit()
    session_id = first.agent_run.harness_run_id
    assert scores_of(first)["gauge_input_tokens"].value is None
    env.deps.sessions = StubSessions(
        {
            session_id: SessionTrajectory(
                settled=True, root_present=True, span_count=3, trajectory=ATIF_DOC
            )
        }
    )
    await evaluate_experiment(env.deps, exp.id, refetch_trajectories=True)
    after = next(r for r in await env.details(exp.id) if r.agent_run_id == first.agent_run_id)
    assert scores_of(after)["gauge_input_tokens"].value
    stored = json.loads(env.blob.objects[f"trajectories/{exp.agent_id}/{session_id}.json"])
    assert stored["extra"]["trajectory_status"] == "valid"


async def test_harness_agent_with_traces_polls_the_session(tmp_path, stub_judges, session_factory):
    fake = FakeHarness(
        capabilities_overrides={"provides": {"trajectory": False, "tool_calls": False}}
    )
    env = await seed_env(session_factory, tmp_path, fake)
    await traced_agent(env, trajectory_wait_seconds=1)
    polled: list[str] = []

    class Sessions:
        async def trajectory(self, agent, session_id):
            polled.append(session_id)
            return SessionTrajectory(
                settled=True, root_present=True, span_count=3, trajectory=ATIF_DOC
            )

    env.deps.sessions = Sessions()
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="traced@canonical"))
    caps = exp.config.snapshot.capabilities
    assert caps["provides"]["trajectory"] and caps["provides"]["tool_calls"]
    assert caps["provides"]["token_usage"]
    assert exp.config.snapshot.trajectory_source is TrajectorySource.OTEL
    assert sorted(polled) == ["sess-1", "sess-2"]
    for run in await env.details(exp.id):
        assert run.agent_run.trajectory_status == "valid"
        assert run.agent_run.trajectory_key
        assert env.blob.objects[run.agent_run.trajectory_key]
        assert scores_of(run)["gauge_input_tokens"].value


async def test_harness_agent_with_traces_releases_the_slot_before_polling(
    tmp_path, stub_judges, session_factory
):
    fake = FakeHarness(capabilities_overrides={"provides": {"trajectory": False}})
    env = await seed_env(session_factory, tmp_path, fake)
    await traced_agent(env, trajectory_wait_seconds=5, max_concurrent_runs=1)
    started: list[str] = []
    gate = asyncio.Event()

    class Sessions:
        async def trajectory(self, agent, session_id):
            started.append(session_id)
            if len(started) < 2:
                await gate.wait()
            gate.set()
            return SessionTrajectory(
                settled=True, root_present=True, span_count=1, trajectory=DEFAULT_TRAJECTORY
            )

    env.deps.sessions = Sessions()
    await start(env, ExperimentRequest(dataset="finance@canonical", agent="traced@canonical"))
    assert len(started) == 2


async def test_rescoring_retries_errored_evaluators_and_replaces_their_error_rows(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    env = await seed_env(session_factory, tmp_path, broken_on_case_2=True)
    real_judge = rubrics.call_judge

    async def forbidden(cfg, **kwargs):
        raise RuntimeError("judge returned 403")

    monkeypatch.setattr(rubrics, "call_judge", forbidden)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    first = assemble_report(await env.store.aggregate(exp.id))
    assert {c.case_id: sorted(c.errors) for c in first.cases} == {
        "case-1": ["answer_rubrics"],
        "case-2": ["answer_rubrics", "broken_eval"],
    }
    assert {s.name: s.errors for s in first.evaluators}["broken_eval"] == 1

    monkeypatch.setattr(rubrics, "call_judge", real_judge)
    fixed = FastAPI()

    @fixed.post("/rtc")
    async def rtc():
        return {"scores": [{"name": "required_tool_calls", "score": 1.0}]}

    @fixed.post("/broken")
    async def broken():
        return {"scores": [{"name": "broken_eval", "score": 1.0}]}

    env.deps.custom_eval_transport = httpx.ASGITransport(app=fixed)
    await evaluate_experiment(env.deps, exp.id)

    case_2 = next(run for run in await env.details(exp.id) if run.case_id == "case-2")
    broken_rows = [e for e in case_2.evaluations if e.name == "broken_eval"]
    assert [(e.value, e.error) for e in broken_rows] == [(1.0, None)]
    assert scores_of(case_2)["answer_rubric_score"].value == 0.5
    assert "answer_rubrics" not in scores_of(case_2)
    report = assemble_report(await env.store.aggregate(exp.id))
    assert all(case.errors == {} for case in report.cases)
    assert all(stat.errors == 0 for stat in report.evaluators)
    assert "answer_rubrics" not in report.evaluator_columns


async def test_rescoring_retries_an_errored_trajectory_judge_once(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    env = await seed_env(session_factory, tmp_path, approved=False)
    trajectory_only = {"trajectory": {"trajectory_rubrics": {"rubrics": ["Looks up revenue."]}}}
    await sync_cases(
        env,
        [
            suite_case("case-1", "q1", evaluators=trajectory_only),
            suite_case("case-2", "q2", evaluators=trajectory_only),
        ],
    )
    await approve(env)
    calls: list[str] = []

    async def forbidden(cfg, **kwargs):
        raise RuntimeError("judge returned 403")

    async def satisfied(cfg, **kwargs):
        calls.append(kwargs["session_id"])
        verdict = trajectory_rubrics.TrajectoryVerdict(index=1, satisfied=True, evidence="e")
        return (
            trajectory_rubrics.TrajectoryJudgeOutput(verdicts=[verdict]),
            JudgeUsage(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(trajectory_rubrics, "call_judge", forbidden)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    first = assemble_report(await env.store.aggregate(exp.id))
    assert all(c.errors == {"trajectory_rubrics": "judge returned 403"} for c in first.cases)

    monkeypatch.setattr(trajectory_rubrics, "call_judge", satisfied)
    await evaluate_experiment(env.deps, exp.id)
    report = assemble_report(await env.store.aggregate(exp.id))
    assert all(case.errors == {} for case in report.cases)
    assert all(stat.errors == 0 for stat in report.evaluators)
    assert len(calls) == 2

    await evaluate_experiment(env.deps, exp.id)
    assert len(calls) == 2


async def test_rescoring_keeps_successful_scores(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path)
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    RECEIVED_EVAL_QUESTIONS.clear()
    await evaluate_experiment(env.deps, exp.id)
    assert RECEIVED_EVAL_QUESTIONS == []


async def test_one_failed_score_publish_lets_the_other_runs_finish(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    env = await seed_env(session_factory, tmp_path)
    exp = await prepare(
        env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical", repetitions=2)
    )
    await drive_experiment(env.deps, exp.id)
    real_publish = env.store.publish_evaluations
    failed: list[int] = []

    async def publish_failing_once(run_id: int, rows: list, *, force: bool, holder=None) -> None:
        if not failed:
            failed.append(run_id)
            raise RuntimeError("POST scores → 502: Bad Gateway")
        await real_publish(run_id, rows, force=force, holder=holder)

    monkeypatch.setattr(env.store, "publish_evaluations", publish_failing_once)
    with pytest.raises(ScoringFailed) as raised:
        await evaluate_experiment(env.deps, exp.id)
    assert raised.value.exp_id == exp.id
    assert "scoring failed for 1 of 4 runs" in str(raised.value)
    assert "RuntimeError: POST scores → 502: Bad Gateway" in str(raised.value)
    assert (await env.experiment(exp.id)).status is ExperimentStatus.RUNNING
    assert [bool(run.evaluations) for run in await env.details(exp.id)].count(True) == 3

    monkeypatch.setattr(env.store, "publish_evaluations", real_publish)
    await env.store.transition(exp.id, ExperimentStatus.EVALUATING, HOLDER)
    await rescore_experiment(env.deps, exp.id, HOLDER)
    assert (await env.experiment(exp.id)).status is COMPLETED
    assert all(run.evaluations for run in await env.details(exp.id))


@pytest.mark.parametrize(
    ("agent", "label"),
    [("fake@canonical", "pass"), ("fake@akshat", "error")],
    ids=["canonical", "akshat"],
)
async def test_remote_evaluators_come_from_the_agents_workspace(
    tmp_path, stub_judges, session_factory, agent, label
):
    env = await seed_env(session_factory, tmp_path)
    working = RemoteEvaluatorSettings(endpoint="http://custom.test/rtc", timeout_seconds=2)
    broken = RemoteEvaluatorSettings(endpoint="http://custom.test/broken", timeout_seconds=2)
    env.deps.evaluators = FakeEvaluators(
        {
            workspace: {
                "required_tool_calls": RemoteEvaluator(
                    name="required_tool_calls",
                    workspace=workspace,
                    kind=EvaluatorKind.SCORE,
                    settings=settings,
                )
            }
            for workspace, settings in (("canonical", working), ("akshat", broken))
        }
    )
    req = ExperimentRequest(
        dataset="finance@canonical",
        agent=agent,
        capabilities=Capabilities.model_validate(FakeHarness().capabilities),
    )
    exp = await start(env, req)
    row = scores_of((await env.details(exp.id))[0])["required_tool_calls"]
    assert row.label == label
    snapshot = exp.config.snapshot.remote_evaluators
    assert snapshot["required_tool_calls"].endpoint.endswith("rtc" if label == "pass" else "broken")


async def test_reevaluation_uses_the_experiments_judge(tmp_path, session_factory, monkeypatch):
    seen: list[tuple[str, str | None]] = []

    async def recording_judge(cfg, **kwargs):
        seen.append((cfg.model, kwargs.get("api_key")))
        return (
            rubrics.RubricJudgeOutput(
                verdicts=[rubrics.RubricVerdict(index=1, verdict="satisfied", source=None)]
            ),
            JudgeUsage(),
        )

    monkeypatch.setattr(rubrics, "call_judge", recording_judge)
    monkeypatch.setattr(presentation, "call_judge", recording_judge)
    monkeypatch.setattr(trajectory_rubrics, "call_judge", recording_judge)
    env = await seed_env(session_factory, tmp_path)
    judge = JudgeSettings(base_url=JUDGE_URL, model="x")
    exp = await start(
        env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical", judge=judge)
    )
    assert exp.config.judge.model == "x"
    assert {model for model, _ in seen} == {"x"} and {key for _, key in seen} == {"jk"}
    seen.clear()
    env.deps.cfg = env.deps.cfg.model_copy(
        update={"judge_default": JudgeSettings(base_url=JUDGE_URL, model="y")}
    )
    await clear_evaluations(env)
    await evaluate_experiment(env.deps, exp.id)
    assert seen and {model for model, _ in seen} == {"x"}


async def test_missing_judge_key_fails_before_any_harness_call(
    tmp_path, stub_judges, session_factory
):
    fake = FakeHarness()
    env = await seed_env(session_factory, tmp_path, fake)
    env.deps.credentials = CredentialRegistry(
        TEST_CREDENTIALS_CONFIG.model_copy(update={"llm_providers": {}}),
        {"FAKE_AGENT_KEY": "client-key"},
    )
    exp = await prepare(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    with pytest.raises(MissingCredential, match=JUDGE_URL):
        await run_experiment(env.deps, exp.id, HOLDER)
    assert fake.runs == {}
    assert (await env.experiment(exp.id)).status is FAILED


async def test_workspace_tree_snapshot_lists_every_file(tmp_path, stub_judges, session_factory):
    files = {"outputs/report.md": b"# report", "scratch/notes.txt": b"n"}
    env = await seed_env(session_factory, tmp_path, FakeHarness(workspace_files=files))
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    for run in await env.details(exp.id):
        tree = json.loads(
            env.blob.objects[f"workspaces/{exp.agent_id}/{run.agent_run.harness_run_id}.json"]
        )
        assert sorted(entry["path"] for entry in tree["files"]) == sorted(files)


async def test_a_failed_workspace_listing_keeps_the_experiment(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    async def down(self, run_id: str):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(HarnessClient, "list_files", down)
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    exp = await start(env, ExperimentRequest(dataset="finance@canonical", agent="fake@canonical"))
    assert (await env.experiment(exp.id)).status is COMPLETED
    assert not any(key.startswith("workspaces/") for key in env.blob.objects)
