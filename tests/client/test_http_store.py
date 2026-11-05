from datetime import UTC, datetime

import pytest

from evalkit.client.api import AsyncApi, VersionMismatch
from evalkit.client.store import HttpStore, local_holder
from evalkit.contracts.enums import EvaluationSource, EvaluatorKind, ExperimentStatus, RunStatus
from evalkit.core.runner import ExperimentRequest
from evalkit.store.base import AgentRunRecord, CaseRep, EvaluationRow, Selection
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent
from tests.native import seed_env, serve


@pytest.fixture
async def ctx(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    await seed_agent(session_factory, "fake", "admin")
    async with serve(env, tmp_path) as client:
        token = client.headers["Authorization"].removeprefix("Bearer ")
        api = AsyncApi("http://test", token, transport=client._transport)
        yield api, env
        await api.aclose()


async def test_store_round_trip_over_http(ctx):
    api, env = ctx
    store = HttpStore(api)
    holder = local_holder("admin")
    picked = await store.cases_for("finance@canonical", Selection(case_ids=["case-2"]), agent=None)
    assert [c.case_id for c in picked] == ["case-2"]
    exp, _ = await store.start_experiment(
        ExperimentRequest(
            dataset="finance@canonical",
            agent="fake@admin",
            capabilities=FakeHarness().capabilities,
            holder=holder,
        )
    )
    assert exp.lease_holder == holder and exp.status is ExperimentStatus.RUNNING
    assert (await store.get_experiment(exp.id)).id == exp.id
    assert {c.case_id for c in await store.pinned_cases(exp.id)} == {"case-1", "case-2"}
    assert await store.pending_runs(exp.id) == [
        CaseRep(case_id="case-1", repetition=1),
        CaseRep(case_id="case-2", repetition=1),
    ]
    ref = await store.record_run(
        exp.id,
        "case-2",
        1,
        AgentRunRecord(
            harness_run_id="s",
            status=RunStatus.COMPLETED,
            answer="a",
            started_at=datetime(2026, 10, 5, tzinfo=UTC),
        ),
        holder=holder,
    )
    assert await store.pending_runs(exp.id) == [CaseRep(case_id="case-1", repetition=1)]
    await store.publish_evaluations(
        ref.experiment_run_id,
        [
            EvaluationRow(
                producer="x",
                name="x",
                kind=EvaluatorKind.SCORE,
                source=EvaluationSource.BUILTIN,
                value=0.5,
            )
        ],
        force=False,
        holder=holder,
    )
    [run] = await store.runs_to_score(exp.id, force=False)
    assert run.done == {"x"} and run.agent_run.answer == "a"
    assert (await store.runs_to_score(exp.id, force=True))[0].done == set()
    await store.set_model(exp.id, "m")
    assert await store.renew_lease(exp.id, holder) is True
    assert await store.renew_lease(exp.id, "someone-else") is False
    await store.transition(exp.id, ExperimentStatus.EVALUATING, holder)
    done = await store.transition(exp.id, ExperimentStatus.COMPLETED, holder)
    assert done.status is ExperimentStatus.COMPLETED and done.config.model == "m"
    [detail] = await store.run_details(exp.id)
    assert detail.evaluations[0].value == 0.5


async def test_require_same_version(ctx, monkeypatch):
    api, _ = ctx
    await api.require_same_version()
    monkeypatch.setattr("evalkit.version.current", lambda: "0.0.1")
    with pytest.raises(VersionMismatch):
        await api.require_same_version()
