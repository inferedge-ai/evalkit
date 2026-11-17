from datetime import timedelta

import httpx2
from pydantic import BaseModel

from evalkit.api.auth import issue_token
from evalkit.contracts.cases import Case
from evalkit.contracts.custom_evaluator import EvalCase, EvalRun, ScoreItem
from evalkit.contracts.enums import DatasetKind, SyncSource
from evalkit.contracts.settings import JudgeSettings
from evalkit.core import python_eval
from evalkit.core.importer import import_cases
from evalkit.core.judge import JudgeUsage
from evalkit.core.python_eval import EvaluatorJudge, PythonEvaluator
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.models import TenantSettings
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.store import PgStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_agents import JUDGE_URL, TEST_BOX, TEST_ENV
from tests.identity import seed_agent
from tests.native import seed_env, serve, wait_for

OTHER_JUDGE_URL = "http://other-judge.test"


class Verdict(BaseModel):
    correct: bool
    reasoning: str


async def ext_answer_match(case: EvalCase, run: EvalRun, judge: EvaluatorJudge) -> list[ScoreItem]:
    verdict = await judge.ask(
        system="Grade the answer.",
        user=f"reference: {case.config['reference_answer']}\nanswer: {run.answer}",
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
