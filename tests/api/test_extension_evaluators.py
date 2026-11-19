from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import httpx2
import pytest
from pydantic import BaseModel

from evalkit.api.auth import issue_token
from evalkit.client.api import AsyncApi
from evalkit.client.server_evaluators import HttpServerEvaluators
from evalkit.contracts.cases import Case
from evalkit.contracts.custom_evaluator import EvalCase, EvalRun, ScoreItem
from evalkit.contracts.enums import (
    DatasetKind,
    EvaluationSource,
    ExperimentStatus,
    ScoredOn,
    SyncSource,
)
from evalkit.contracts.settings import JudgeSettings
from evalkit.core import python_eval
from evalkit.core.importer import import_cases
from evalkit.core.judge import JudgeUsage
from evalkit.core.python_eval import EvaluatorJudge, PythonEvaluator
from evalkit.core.runner import ExperimentRequest, evaluate_experiment
from evalkit.credentials import CredentialRegistry
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.models import TenantSettings
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.store import PgStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_agents import JUDGE_URL, TEST_BOX, TEST_CREDENTIALS_CONFIG, TEST_ENV
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent
from tests.native import seed_env, serve, start, wait_for

OTHER_JUDGE_URL = "http://other-judge.test"


class Verdict(BaseModel):
    correct: bool
    reasoning: str


async def ext_answer_match(case: EvalCase, run: EvalRun, judge: EvaluatorJudge) -> list[ScoreItem]:
    verdict = await judge.ask(
        system_prompt="Grade the answer.",
        user_prompt=f"reference: {case.config['reference_answer']}\nanswer: {run.answer}",
        output_type=Verdict,
    )
    return [
        ScoreItem(
            name="ext_answer_match",
            score=1.0 if verdict.correct else 0.0,
            explanation=verdict.reasoning,
        )
    ]


def ext_length(case: EvalCase, run: EvalRun) -> float:
    return 1.0 if run.answer else 0.0


EXTENSION_EVALUATORS = {
    "ext_answer_match": PythonEvaluator(
        name="ext_answer_match", kind="evaluator", fn=ext_answer_match, uses_judge=True
    ),
    "ext_length": PythonEvaluator(name="ext_length", kind="evaluator", fn=ext_length),
}


def extension_case(case_id: str, evaluators: dict) -> Case:
    return Case.model_validate(
        {"id": case_id, "input": {"question": "Who?"}, "evaluators": {"custom": evaluators}}
    )


async def write_dataset(session_factory, tenant_id: str, actor_id: int, cases: list[Case]):
    workspace = await WorkspaceStore(session_factory, tenant_id).get_by_name("canonical")
    return await import_cases(
        PgStore(session_factory, tenant_id),
        workspace=workspace,
        dataset="ext",
        kind=DatasetKind.SUITE,
        sourced_cases=[(case, None) for case in cases],
        overlays=[],
        findings=[],
        full=True,
        known_custom_evaluators=set(EXTENSION_EVALUATORS),
        actor_id=actor_id,
        source=SyncSource.EXTENSION,
        detail={"written_by": "tests", "revision": 1},
    )


async def second_tenant(client, raw_session_factory) -> httpx2.AsyncClient:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(TenantSettings(tenant_id="tenant-2"))
        await s.commit()
    root = await UserStore(raw_session_factory, "tenant-2").create(
        "root", password="password", is_admin=True
    )
    await WorkspaceStore(raw_session_factory, "tenant-2").create_shared("canonical", root.id)
    providers = LlmProviderStore(raw_session_factory, "tenant-2")
    await providers.create("theirs", OTHER_JUDGE_URL, TEST_BOX.seal("their-key"))
    await providers.set_default_judge(JudgeSettings(provider="theirs", model="their-model"))
    await seed_agent(raw_session_factory, tenant_id="tenant-2")
    await write_dataset(
        raw_session_factory,
        "tenant-2",
        root.id,
        [extension_case("ext-9", {"ext_answer_match": {"reference_answer": "Ada"}})],
    )
    other = httpx2.AsyncClient(transport=client._transport, base_url="http://test")
    async with tenant_session(raw_session_factory, "tenant-2") as session:
        token = await issue_token(session, root, timedelta(hours=1))
    other.headers["Authorization"] = f"Bearer {token}"
    return other


async def run_ext(client: httpx2.AsyncClient) -> tuple[dict, list[dict]]:
    started = await client.post(
        "/api/experiments", json={"dataset": "ext@canonical", "agent": "fake@canonical"}
    )
    assert started.status_code == 200, started.text
    exp = await wait_for(client, started.json()["id"])
    assert exp["status"] == "completed", exp
    return exp, (await client.get(f"/api/experiments/{exp['id']}/runs")).json()


async def test_an_extension_writes_a_dataset_with_its_own_sync_source(tmp_path, session_factory):
    env = await seed_env(session_factory, tmp_path)
    report = await write_dataset(
        session_factory,
        "default",
        env.user_ids["admin"],
        [extension_case("ext-1", {"ext_length": {}})],
    )
    assert (report.created, report.aborted) == (1, False)
    async with serve(env, tmp_path, extension_evaluators=EXTENSION_EVALUATORS) as client:
        dataset = (await client.get("/api/datasets/ext@canonical")).json()
        assert dataset["last_sync"]["source"] == "extension"
        assert dataset["last_sync"]["detail"] == {
            "written_by": "tests",
            "revision": 1,
            "counts": {"created": 1, "patched": 0, "removed": 0, "unchanged": 0},
        }
        listed = (await client.get("/api/datasets", params={"workspace": "canonical"})).json()
        assert {d["name"]: d["last_sync"]["source"] for d in listed}["ext"] == "extension"


async def test_an_extension_evaluator_grades_with_each_tenants_own_judge(
    tmp_path, session_factory, raw_session_factory, monkeypatch
):
    asked: list[tuple[str | None, str | None, str, str]] = []

    async def fake_call_judge(cfg, **kwargs):
        asked.append((cfg.base_url, kwargs["api_key"], cfg.model, kwargs["evaluator_name"]))
        verdict = Verdict(correct="Ada" in kwargs["user_prompt"], reasoning="checked")
        return verdict, JudgeUsage(input_tokens=1000, output_tokens=100)

    monkeypatch.setattr(python_eval, "call_judge", fake_call_judge)
    env = await seed_env(session_factory, tmp_path)
    await write_dataset(
        session_factory,
        "default",
        env.user_ids["admin"],
        [
            extension_case(
                "ext-1", {"ext_answer_match": {"reference_answer": "Bob"}, "ext_length": {}}
            )
        ],
    )
    async with serve(env, tmp_path, extension_evaluators=EXTENSION_EVALUATORS) as client:
        _, [run] = await run_ext(client)
        rows = {e["name"]: e for e in run["evaluations"]}
        assert (rows["ext_answer_match"]["value"], rows["ext_answer_match"]["explanation"]) == (
            0.0,
            "checked",
        )
        assert rows["ext_answer_match"]["source"] == "extension"
        assert rows["ext_answer_match"]["scored_on"] == "server"
        assert rows["ext_length"]["value"] == 1.0
        assert rows["gauge_judge_cost_usd"]["value"] > 0
        assert asked == [(JUDGE_URL, TEST_ENV["JUDGE_KEY"], "judge-model", "ext_answer_match")]

        asked.clear()
        other = await second_tenant(client, raw_session_factory)
        exp, [run] = await run_ext(other)
        assert exp["config"]["judge"]["provider"] == "theirs"
        assert {e["name"]: e["value"] for e in run["evaluations"]}["ext_answer_match"] == 1.0
        assert asked == [(OTHER_JUDGE_URL, "their-key", "their-model", "ext_answer_match")]


async def test_a_judge_using_extension_evaluator_needs_a_judge(
    tmp_path, session_factory, raw_session_factory
):
    env = await seed_env(session_factory, tmp_path)
    await write_dataset(
        session_factory,
        "default",
        env.user_ids["admin"],
        [extension_case("ext-1", {"ext_answer_match": {"reference_answer": "Bob"}})],
    )
    async with tenant_session(raw_session_factory, "default") as s:
        settings = await s.get(TenantSettings, "default")
        assert settings is not None
        settings.default_judge = None
        await s.commit()
    async with serve(env, tmp_path, extension_evaluators=EXTENSION_EVALUATORS) as client:
        refused = await client.post(
            "/api/experiments", json={"dataset": "ext@canonical", "agent": "fake@canonical"}
        )
        assert refused.status_code == 400
        assert refused.json()["detail"].startswith("no judge:")
    async with serve(env, tmp_path) as client:
        started = await client.post(
            "/api/experiments", json={"dataset": "ext@canonical", "agent": "fake@canonical"}
        )
        assert started.status_code == 200, started.text
        await wait_for(client, started.json()["id"])
        [run] = (await client.get(f"/api/experiments/{started.json()['id']}/runs")).json()
        rows = {e["name"]: e for e in run["evaluations"]}
        assert rows["ext_answer_match"]["error"] == (
            "custom evaluator ext_answer_match is not configured"
        )


async def test_a_workspaces_own_remote_evaluator_wins_over_an_extensions(tmp_path, session_factory):
    env = await seed_env(session_factory, tmp_path)
    await write_dataset(
        session_factory,
        "default",
        env.user_ids["admin"],
        [extension_case("ext-1", {"required_tool_calls": {"tools": ["retrieve_passages"]}})],
    )
    shadow = PythonEvaluator(name="required_tool_calls", kind="evaluator", fn=ext_length)
    async with serve(env, tmp_path, extension_evaluators={"required_tool_calls": shadow}) as client:
        _, [run] = await run_ext(client)
        rows = {e["name"]: e for e in run["evaluations"]}
        assert rows["required_tool_calls"]["source"] == "remote"
        assert rows["tool_call_efficiency"]["value"] == 0.5


class Bearer(httpx2.Auth):
    def __init__(self, header: str):
        self.header = header

    def auth_flow(self, request):
        request.headers["Authorization"] = self.header
        yield request


@asynccontextmanager
async def local_runner(tmp_path, session_factory, cases: list[Case]):
    env = await seed_env(session_factory, tmp_path)
    await write_dataset(session_factory, "default", env.user_ids["admin"], cases)
    await seed_agent(session_factory, "fake", "admin")
    agents = {**TEST_CREDENTIALS_CONFIG.agents, "fake@admin": "env:FAKE_AGENT_KEY"}
    async with serve(env, tmp_path, extension_evaluators=EXTENSION_EVALUATORS) as client:
        api = AsyncApi(
            "http://test", Bearer(client.headers["Authorization"]), transport=client._transport
        )
        env.deps = replace(
            env.deps,
            server_evaluators=HttpServerEvaluators(api),
            credentials=CredentialRegistry(
                TEST_CREDENTIALS_CONFIG.model_copy(update={"agents": agents}), TEST_ENV
            ),
        )
        assert not env.deps.extension_evaluators
        yield client, env
        await api.aclose()


def judge_recorder(monkeypatch, *, fail: bool = False) -> list[tuple[str | None, str | None]]:
    asked: list[tuple[str | None, str | None]] = []

    async def fake_call_judge(cfg, **kwargs):
        asked.append((cfg.base_url, kwargs["api_key"]))
        if fail:
            raise ConnectionError("judge down")
        verdict = Verdict(correct="Ada" in kwargs["user_prompt"], reasoning="checked")
        return verdict, JudgeUsage(input_tokens=1000, output_tokens=100)

    monkeypatch.setattr(python_eval, "call_judge", fake_call_judge)
    return asked


BOTH = [
    extension_case("ext-1", {"ext_answer_match": {"reference_answer": "Bob"}, "ext_length": {}})
]
LOCAL_RUN = ExperimentRequest(
    dataset="ext@canonical",
    agent="fake@admin",
    judge=JudgeSettings(provider="judge", model="judge-model"),
)


async def test_the_server_lists_its_extension_evaluators(tmp_path, session_factory):
    env = await seed_env(session_factory, tmp_path)
    async with serve(env, tmp_path, extension_evaluators=EXTENSION_EVALUATORS) as client:
        assert (await client.get("/api/extension-evaluators")).json() == [
            {"name": "ext_answer_match", "uses_judge": True, "requires_trajectory": False},
            {"name": "ext_length", "uses_judge": False, "requires_trajectory": False},
        ]
    async with serve(env, tmp_path) as client:
        assert (await client.get("/api/extension-evaluators")).json() == []


async def test_a_local_run_gets_its_extension_scores_from_the_server(
    tmp_path, session_factory, monkeypatch
):
    asked = judge_recorder(monkeypatch)
    async with local_runner(tmp_path, session_factory, BOTH) as (client, env):
        exp = await start(env, LOCAL_RUN)
        assert exp.status is ExperimentStatus.COMPLETED, exp.status_error
        [run] = await env.details(exp.id)
        rows = {e.name: e for e in run.evaluations}
        assert (rows["ext_answer_match"].value, rows["ext_answer_match"].explanation) == (
            0.0,
            "checked",
        )
        assert rows["ext_length"].value == 1.0
        for name in ("ext_answer_match", "ext_length"):
            assert rows[name].source is EvaluationSource.EXTENSION
            assert rows[name].scored_on is ScoredOn.SERVER
        assert rows["gauge_judge_cost_usd"].value > 0
        assert asked == [(JUDGE_URL, TEST_ENV["JUDGE_KEY"])]

        unknown = await client.post(
            f"/api/experiment-runs/{run.experiment_run_id}/server-evaluations/ext_missing",
            json={"config": {}},
        )
        assert unknown.status_code == 404

        asked.clear()
        made_up = await client.post(
            f"/api/experiment-runs/{run.experiment_run_id}/server-evaluations/ext_answer_match",
            json={"config": {"reference_answer": "Ada"}},
        )
        assert made_up.json()["rows"][0]["score"] == 0.0
        assert len(asked) == 1


async def test_re_scoring_a_local_run_asks_the_server_again(tmp_path, session_factory, monkeypatch):
    judge_recorder(monkeypatch, fail=True)
    async with local_runner(tmp_path, session_factory, BOTH) as (_, env):
        exp = await start(env, LOCAL_RUN)
        [run] = await env.details(exp.id)
        rows = {e.name: e for e in run.evaluations}
        assert "judge down" in rows["ext_answer_match"].error
        assert rows["ext_length"].value == 1.0

        asked = judge_recorder(monkeypatch)
        await evaluate_experiment(env.deps, exp.id)
        [run] = await env.details(exp.id)
        rows = {e.name: e for e in run.evaluations}
        assert (rows["ext_answer_match"].value, rows["ext_answer_match"].error) == (0.0, None)
        assert rows["ext_answer_match"].scored_on is ScoredOn.SERVER
        assert len(asked) == 1


async def test_a_local_run_with_its_own_judge_cannot_use_a_judging_extension_evaluator(
    tmp_path, session_factory, monkeypatch
):
    asked = judge_recorder(monkeypatch)
    async with local_runner(tmp_path, session_factory, BOTH) as (client, env):
        own = JudgeSettings(base_url=JUDGE_URL, model="judge-model")
        exp = await start(env, LOCAL_RUN.model_copy(update={"judge": own}))
        assert exp.status is ExperimentStatus.COMPLETED, exp.status_error
        [run] = await env.details(exp.id)
        rows = {e.name: e for e in run.evaluations}
        assert rows["ext_answer_match"].value is None
        assert rows["ext_answer_match"].error == (
            "this dataset needs a judge configured on the server: ext_answer_match is graded "
            "there, not with a judge you bring yourself"
        )
        assert rows["ext_answer_match"].scored_on is ScoredOn.LOCAL
        assert rows["ext_length"].value == 1.0
        assert rows["ext_length"].scored_on is ScoredOn.SERVER
        assert asked == []

        direct = await client.post(
            f"/api/experiment-runs/{run.experiment_run_id}/server-evaluations/ext_answer_match",
            json={"config": {"reference_answer": "Bob"}},
        )
        assert direct.status_code == 400
        assert direct.json()["detail"] == rows["ext_answer_match"].error


async def test_the_server_scores_only_an_extension_evaluator_the_case_names(
    tmp_path, session_factory, monkeypatch
):
    asked = judge_recorder(monkeypatch)
    only_length = [extension_case("ext-1", {"ext_length": {}})]
    async with local_runner(tmp_path, session_factory, only_length) as (client, env):
        exp = await start(env, LOCAL_RUN)
        [run] = await env.details(exp.id)
        refused = await client.post(
            f"/api/experiment-runs/{run.experiment_run_id}/server-evaluations/ext_answer_match",
            json={"config": {"reference_answer": "Ada"}},
        )
        assert refused.status_code == 400
        assert refused.json()["detail"] == "case ext-1 does not name the evaluator ext_answer_match"
        assert asked == []


async def test_an_extension_evaluator_cannot_take_a_built_ins_name(tmp_path, session_factory):
    env = await seed_env(session_factory, tmp_path)
    clash = {
        "answer_rubrics": PythonEvaluator(name="answer_rubrics", kind="evaluator", fn=ext_length)
    }
    with pytest.raises(ValueError, match="named like built-ins: answer_rubrics"):
        async with serve(env, tmp_path, extension_evaluators=clash):
            pass


OWN_JUDGE_BODY = {
    "dataset": "ext@canonical",
    "agent": "fake@admin",
    "capabilities": FakeHarness().capabilities,
    "holder": "cli:admin@laptop:1",
    "judge": {"base_url": JUDGE_URL, "model": "judge-model"},
}


async def test_a_judge_the_user_brings_is_refused_before_any_run_for_a_judging_extension_evaluator(
    tmp_path, session_factory
):
    async with local_runner(tmp_path, session_factory, BOTH) as (client, _):
        refused = await client.post("/api/experiments", json=OWN_JUDGE_BODY)
        assert refused.status_code == 400
        assert refused.json()["detail"] == (
            "this dataset needs a judge configured on the server: ext_answer_match is graded "
            "there, not with a judge you bring yourself"
        )
        assert (await client.get("/api/experiments")).json() == []

        own_code = {**OWN_JUDGE_BODY, "code_evaluators": ["ext_answer_match"]}
        assert (await client.post("/api/experiments", json=own_code)).status_code == 200


async def test_a_judge_the_user_brings_still_runs_built_ins_and_python_evaluators(
    tmp_path, session_factory
):
    mixed = Case.model_validate(
        {
            "id": "ext-1",
            "input": {"question": "Who?"},
            "evaluators": {
                "output": {
                    "answer_rubrics": {
                        "rubrics": [{"rubric": "Names a person.", "type": "factual_retrieval"}]
                    }
                },
                "custom": {"ext_length": {}, "my_check": {}},
            },
        }
    )
    async with local_runner(tmp_path, session_factory, [mixed]) as (client, _):
        body = {**OWN_JUDGE_BODY, "code_evaluators": ["my_check"]}
        created = await client.post("/api/experiments", json=body)
        assert created.status_code == 200, created.text
        assert created.json()["config"]["judge"]["base_url"] == JUDGE_URL


async def test_without_a_judge_a_run_whose_own_evaluator_replaces_the_judging_one_is_created(
    tmp_path, session_factory, raw_session_factory
):
    async with local_runner(tmp_path, session_factory, BOTH) as (client, _):
        async with tenant_session(raw_session_factory, "default") as s:
            settings = await s.get(TenantSettings, "default")
            assert settings is not None
            settings.default_judge = None
            await s.commit()
        body = {key: value for key, value in OWN_JUDGE_BODY.items() if key != "judge"}

        refused = await client.post("/api/experiments", json=body)
        assert refused.status_code == 400
        assert refused.json()["detail"].startswith("no judge:")

        own = {**body, "code_evaluators": ["ext_answer_match"]}
        created = await client.post("/api/experiments", json=own)
        assert created.status_code == 200, created.text
