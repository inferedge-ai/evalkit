import pytest

from evalkit.db.models import ServerSettings
from tests.api.native_api import api_env, login_as
from tests.fake_agents import JUDGE_URL
from tests.fake_harness import FakeHarness

RUN = {"dataset": "finance@canonical", "agent": "fake@canonical"}


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        yield client


async def test_admins_manage_providers_and_keys_stay_hidden(api):
    alice = await login_as(api, "alice", "password")
    body = {"name": "openai", "base_url": "https://api.openai.com/v1", "api_key": "sk-secret"}
    assert (await alice.post("/api/llm-providers", json=body)).status_code == 403
    created = await api.post("/api/llm-providers", json=body)
    assert created.status_code == 200, created.text
    assert created.json() == {
        "name": "openai",
        "base_url": "https://api.openai.com/v1",
        "has_api_key": True,
        "archived": False,
    }
    listed = await alice.get("/api/llm-providers")
    assert "sk-secret" not in listed.text
    assert {p["name"] for p in listed.json()} == {"judge", "openai"}
    assert (await api.post("/api/llm-providers", json=body)).status_code == 409
    moved = await api.patch("/api/llm-providers/openai", json={"base_url": "https://o2/v1"})
    assert moved.json()["has_api_key"] is False
    in_use = await api.post("/api/llm-providers/judge/archive")
    assert in_use.status_code == 409 and "default judge" in in_use.json()["detail"]
    await alice.aclose()


async def test_the_default_judge_is_set_by_admins(api):
    alice = await login_as(api, "alice", "password")
    judge = (await alice.get("/api/judge-default")).json()
    assert judge["base_url"] == JUDGE_URL
    new = {**judge, "model": "bigger"}
    assert (await alice.put("/api/judge-default", json=new)).status_code == 403
    assert (await api.put("/api/judge-default", json=new)).json()["model"] == "bigger"
    unknown = await api.put("/api/judge-default", json={**new, "base_url": "http://nope"})
    assert unknown.status_code == 404
    await alice.aclose()


async def test_server_runs_need_a_registered_judge_and_a_default(api, session_factory):
    unregistered = await api.post(
        "/api/experiments", json={**RUN, "judge": {"base_url": "http://other", "model": "m"}}
    )
    assert unregistered.status_code == 400
    assert (
        "not a registered LLM provider; registered: " + JUDGE_URL in (unregistered.json()["detail"])
    )
    async with session_factory() as session:
        settings = await session.get(ServerSettings, 1)
        assert settings is not None
        settings.default_judge = None
        await session.commit()
    assert (await api.get("/api/judge-default")).status_code == 404
    no_default = await api.post("/api/experiments", json=RUN)
    assert no_default.status_code == 400 and "no default judge" in no_default.json()["detail"]


async def test_keyless_endpoints_warn_but_run(api):
    await api.patch("/api/agents/fake@canonical", json={"api_key": None})
    await api.patch("/api/llm-providers/judge", json={"api_key": None})
    started = await api.post("/api/experiments", json=RUN)
    assert started.status_code == 502
    await api.patch("/api/agents/fake@canonical", json={"api_key": "client-key"})
    started = await api.post("/api/experiments", json=RUN)
    assert started.status_code == 200, started.text
    assert started.json()["warnings"] == [
        f"LLM provider {JUDGE_URL} has no API key; judging without one"
    ]


async def test_private_endpoints_are_refused_unless_allowed(api):
    api._transport.app.state.cfg.allow_private_endpoints = False  # type: ignore[attr-defined]
    blocked = await api.post(
        "/api/agents",
        json={"workspace": "canonical", "name": "meta", "harness_url": "http://169.254.169.254/"},
    )
    assert blocked.status_code == 400 and "allow_private_endpoints" in blocked.json()["detail"]
    provider = await api.post(
        "/api/llm-providers", json={"name": "local", "base_url": "http://127.0.0.1:11434/v1"}
    )
    assert provider.status_code == 400


async def test_judge_default_patch_keeps_unset_fields(api):
    await api.put(
        "/api/judge-default",
        json={"base_url": JUDGE_URL, "model": "judge-model", "concurrency": 4},
    )
    patched = await api.patch("/api/judge-default", json={"model": "bigger"})
    assert patched.json()["model"] == "bigger" and patched.json()["concurrency"] == 4


async def test_an_empty_key_clears_it_everywhere(api):
    await api.post(
        "/api/evaluators",
        json={
            "workspace": "canonical",
            "name": "keyed",
            "kind": "score",
            "settings": {"endpoint": "http://e"},
            "api_key": "k",
        },
    )
    evaluator = await api.patch("/api/evaluators/keyed@canonical", json={"api_key": ""})
    assert evaluator.json()["has_api_key"] is False
    provider = await api.patch("/api/llm-providers/judge", json={"api_key": ""})
    assert provider.json()["has_api_key"] is False
    agent = await api.patch("/api/agents/fake@canonical", json={"api_key": ""})
    assert agent.json()["has_api_key"] is False


async def test_personal_endpoints_skip_the_private_address_check(api):
    api._transport.app.state.cfg.allow_private_endpoints = False  # type: ignore[attr-defined]
    local = {"workspace": "admin", "name": "local", "harness_url": "http://localhost:8080/v1"}
    assert (await api.post("/api/agents", json=local)).status_code == 200
    evaluator = {
        "workspace": "admin",
        "name": "local_eval",
        "kind": "score",
        "settings": {"endpoint": "http://localhost:9000/evaluate"},
    }
    assert (await api.post("/api/evaluators", json=evaluator)).status_code == 200


async def test_resume_and_evaluate_refuse_a_judge_that_is_no_longer_registered(api):
    await api.post("/api/llm-providers", json={"name": "other", "base_url": "http://other/v1"})
    started = await api.post(
        "/api/experiments", json={**RUN, "judge": {"base_url": "http://other/v1", "model": "m"}}
    )
    assert started.status_code == 200, started.text
    await api.post("/api/llm-providers/other/archive")
    exp_id = started.json()["id"]
    for action in ("resume", "evaluate"):
        refused = await api.post(f"/api/experiments/{exp_id}/{action}")
        assert refused.status_code == 400
        assert "http://other/v1 is not a registered LLM provider" in refused.json()["detail"]
