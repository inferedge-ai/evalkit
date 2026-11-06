from dataclasses import replace

import httpx2
import pytest

from evalkit.client.api import AsyncApi
from evalkit.client.server_evaluators import HttpServerEvaluators
from evalkit.contracts.enums import ExperimentStatus, ScoredOn
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.evaluators import rubrics
from evalkit.core.judge import JudgeUsage
from evalkit.core.runner import ExperimentRequest
from evalkit.credentials import CredentialRegistry
from tests.api.native_api import api_env, login_as
from tests.fake_agents import JUDGE_URL, TEST_CREDENTIALS_CONFIG, TEST_ENV
from tests.native import start

PROVIDER = JudgeSettings(provider="judge", model="judge-model")


class Bearer(httpx2.Auth):
    def __init__(self, header: str):
        self.header = header

    def auth_flow(self, request):
        request.headers["Authorization"] = self.header
        yield request


@pytest.fixture
async def served(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        api = AsyncApi(
            "http://test", Bearer(client.headers["Authorization"]), transport=client._transport
        )
        agents = {**TEST_CREDENTIALS_CONFIG.agents, "fake@admin": "env:FAKE_AGENT_KEY"}
        env.deps = replace(
            env.deps,
            server_evaluators=HttpServerEvaluators(api),
            credentials=CredentialRegistry(
                TEST_CREDENTIALS_CONFIG.model_copy(update={"agents": agents}), TEST_ENV
            ),
        )
        yield client, env
        await api.aclose()


async def _local_run(env, judge: JudgeSettings):
    request = ExperimentRequest(dataset="finance@canonical", agent="fake@admin", judge=judge)
    exp = await start(env, request)
    assert exp.status is ExperimentStatus.COMPLETED, exp.status_error
    return exp


async def test_a_local_run_scores_judge_builtins_on_the_server_with_the_stored_key(
    served, monkeypatch
):
    client, env = served
    seen: list[tuple[str | None, str | None]] = []
    stubbed = rubrics.call_judge

    async def recording(cfg, **kwargs):
        seen.append((cfg.base_url, kwargs["api_key"]))
        return await stubbed(cfg, **kwargs)

    monkeypatch.setattr(rubrics, "call_judge", recording)
    exp = await _local_run(env, PROVIDER)
    assert seen and set(seen) == {(JUDGE_URL, TEST_ENV["JUDGE_KEY"])}
    run = (await env.details(exp.id))[0]
    rows = {e.name: e for e in run.evaluations}
    assert rows["answer_rubric_score"].scored_on is ScoredOn.SERVER
    assert rows["max_response_time"].scored_on is ScoredOn.LOCAL
    assert rows["gauge_judge_cost_usd"].value is not None

    before = len(run.evaluations)
    direct = await client.post(
        f"/api/experiment-runs/{run.experiment_run_id}/server-evaluations/answer_rubrics",
        json={"config": {"rubrics": [{"rubric": "Says hi.", "type": "factual_retrieval"}]}},
    )
    assert direct.status_code == 200, direct.text
    body = direct.json()
    assert {r["name"] for r in body["rows"]} >= {"answer_rubric_score"}
    assert body["judge_usage"] == JudgeUsage(input_tokens=100, output_tokens=10).model_dump()
    assert len((await env.details(exp.id))[0].evaluations) == before


async def test_server_evaluations_refusals(served):
    client, env = served
    exp = await _local_run(env, PROVIDER)
    run_id = (await env.details(exp.id))[0].experiment_run_id
    url = f"/api/experiment-runs/{run_id}/server-evaluations"
    unknown = await client.post(f"{url}/nope", json={"config": {}})
    assert unknown.status_code == 404
    local_only = await client.post(f"{url}/tool_trajectory_match", json={"config": {}})
    assert local_only.status_code == 400 and "your machine" in local_only.json()["detail"]
    alice = await login_as(client, "alice", "password")
    assert (await alice.post(f"{url}/answer_rubrics", json={"config": {}})).status_code == 403
    await alice.aclose()

    byok = await _local_run(env, JudgeSettings(base_url=JUDGE_URL, model="judge-model"))
    byok_run = (await env.details(byok.id))[0].experiment_run_id
    refused = await client.post(
        f"/api/experiment-runs/{byok_run}/server-evaluations/answer_rubrics", json={"config": {}}
    )
    assert refused.status_code == 400 and "no provider judge" in refused.json()["detail"]


async def test_builtin_evaluators_are_listed_and_their_names_reserved(served):
    client, _ = served
    listed = (await client.get("/api/builtin-evaluators")).json()
    assert {"name": "answer_rubrics", "uses_judge": True, "local_only": False} in listed
    assert {"name": "max_response_time", "uses_judge": False, "local_only": True} in listed
    clash = await client.post(
        "/api/evaluators",
        json={
            "workspace": "admin",
            "name": "answer_rubrics",
            "kind": "score",
            "settings": {"endpoint": "http://e"},
        },
    )
    assert clash.status_code == 400 and "built-in" in clash.json()["detail"]


async def test_a_private_personal_harness_url_does_not_block_server_evaluations(served):
    client, env = served
    exp = await _local_run(env, PROVIDER)
    run_id = (await env.details(exp.id))[0].experiment_run_id
    client.app.state.cfg.allow_private_endpoints = False  # type: ignore[attr-defined]
    scored = await client.post(
        f"/api/experiment-runs/{run_id}/server-evaluations/answer_rubrics",
        json={"config": {"rubrics": [{"rubric": "Says hi.", "type": "factual_retrieval"}]}},
    )
    assert scored.status_code == 200, scored.text
    assert all(row["error"] is None for row in scored.json()["rows"])
