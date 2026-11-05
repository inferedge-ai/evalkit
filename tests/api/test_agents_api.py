import pytest

from tests.api.native_api import CAPS, api_env, login_as
from tests.fake_agents import reference_mapping
from tests.fake_harness import FakeHarness


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        await client.post("/api/users", json={"username": "bob", "password": "password"})
        await client.post("/api/workspaces", json={"name": "dev"})
        yield client


async def test_shared_agents_need_admin_or_writer(api):
    bob = await login_as(api, "bob", "password")
    body = {"workspace": "dev", "name": "ouragent", "harness_url": "http://ouragent"}
    assert (await bob.post("/api/agents", json=body)).status_code == 403
    created = await api.post("/api/agents", json=body)
    assert created.status_code == 200, created.text
    assert created.json()["driver"] == "server"
    assert (await api.post("/api/agents", json=body)).status_code == 409
    await api.post("/api/workspaces/dev/members", json={"username": "bob", "role": "writer"})
    edited = await bob.patch("/api/agents/ouragent@dev", json={"repo_url": "https://git/x"})
    assert edited.status_code == 200 and edited.json()["repo_url"] == "https://git/x"
    await bob.aclose()


async def test_personal_agents_belong_to_their_owner(api):
    bob = await login_as(api, "bob", "password")
    body = {"workspace": "bob", "name": "myagent", "harness_url": "http://localhost:9000"}
    created = await bob.post("/api/agents", json=body)
    assert created.status_code == 200 and created.json()["driver"] == "cli"
    settings = {"settings": {"max_concurrent_runs": 1}}
    assert (await api.patch("/api/agents/myagent@bob", json=settings)).status_code == 403
    assert (await api.get("/api/agents/myagent@bob")).status_code == 200
    resp = await bob.post(
        "/api/experiments",
        json={"dataset": "finance", "agent": "fake@alice", "capabilities": CAPS},
    )
    assert resp.status_code == 403
    await bob.aclose()


async def test_otel_agents_need_a_mapping_and_keep_their_trajectory_source(api):
    body = {"workspace": "dev", "name": "traced", "trajectory_source": "otel"}
    missing = await api.post("/api/agents", json=body)
    assert missing.status_code == 400 and "mapping" in missing.json()["detail"]
    created = await api.post("/api/agents", json={**body, "mapping": reference_mapping()})
    assert created.status_code == 200, created.text
    mappings = (await api.get("/api/agents/traced@dev/mappings")).json()
    assert [(m["version"], m["status"]) for m in mappings] == [(1, "active")]


async def test_trajectory_source_switches_in_place(api):
    created = await api.post(
        "/api/agents",
        json={
            "workspace": "dev",
            "name": "bot",
            "settings": {"provides": {"thinking": True}},
        },
    )
    assert created.status_code == 200, created.text
    no_mapping = await api.patch("/api/agents/bot@dev", json={"trajectory_source": "otel"})
    assert no_mapping.status_code == 400
    assert "no active mapping" in no_mapping.json()["detail"]
    to_otel = await api.patch(
        "/api/agents/bot@dev", json={"trajectory_source": "otel", "mapping": reference_mapping()}
    )
    assert to_otel.status_code == 200, to_otel.text
    assert to_otel.json()["trajectory_source"] == "otel"
    mappings = (await api.get("/api/agents/bot@dev/mappings")).json()
    assert [(m["version"], m["status"]) for m in mappings] == [(1, "active")]

    to_atif = await api.patch("/api/agents/bot@dev", json={"trajectory_source": "atif"})
    assert to_atif.status_code == 200 and to_atif.json()["trajectory_source"] == "atif"
    back = await api.patch("/api/agents/bot@dev", json={"trajectory_source": "otel"})
    assert back.status_code == 200 and back.json()["trajectory_source"] == "otel"
    mappings = (await api.get("/api/agents/bot@dev/mappings")).json()
    assert [(m["version"], m["status"]) for m in mappings] == [(1, "active")]

    replaced = await api.patch("/api/agents/bot@dev", json={"mapping": reference_mapping()})
    assert replaced.status_code == 200
    mappings = (await api.get("/api/agents/bot@dev/mappings")).json()
    assert sorted((m["version"], m["status"]) for m in mappings) == [
        (1, "retired"),
        (2, "active"),
    ]
    atif_mapping = await api.patch(
        "/api/agents/bot@dev", json={"trajectory_source": "atif", "mapping": reference_mapping()}
    )
    assert atif_mapping.status_code == 400


async def test_switching_to_atif_drops_provides_next_to_a_harness(api):
    created = await api.post(
        "/api/agents",
        json={
            "workspace": "dev",
            "name": "hybrid",
            "harness_url": "http://hybrid",
            "trajectory_source": "otel",
            "mapping": reference_mapping(),
            "settings": {"provides": {"thinking": True}},
        },
    )
    assert created.status_code == 200, created.text
    current = (await api.get("/api/agents/hybrid@dev")).json()
    assert current["settings"]["provides"] == {"thinking": True}
    switched = await api.patch(
        "/api/agents/hybrid@dev",
        json={"trajectory_source": "atif", "settings": current["settings"]},
    )
    assert switched.status_code == 200, switched.text
    assert switched.json()["settings"]["provides"] is None


async def test_trajectory_source_does_not_change_under_a_running_experiment(api):
    started = await api.post(
        "/api/experiments",
        json={
            "dataset": "finance@canonical",
            "agent": "fake@admin",
            "capabilities": CAPS,
            "holder": "local:admin@laptop:1",
        },
    )
    assert started.status_code == 200, started.text
    busy = await api.patch(
        "/api/agents/fake@admin",
        json={"trajectory_source": "otel", "mapping": reference_mapping()},
    )
    assert busy.status_code == 409 and "running experiment" in busy.json()["detail"]
    same = await api.patch("/api/agents/fake@admin", json={"repo_url": "https://git/x"})
    assert same.status_code == 200


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,x", "ftp://git/x", "git"])
async def test_repo_url_must_be_http(api, url):
    created = await api.post(
        "/api/agents", json={"workspace": "admin", "name": "badrepo", "repo_url": url}
    )
    assert created.status_code == 422
    edited = await api.patch("/api/agents/fake@admin", json={"repo_url": url})
    assert edited.status_code == 422


async def test_archived_agents_are_hidden_and_cannot_run(api):
    archived = await api.post("/api/agents/fake@admin/archive")
    assert archived.status_code == 200 and archived.json()["archived"]
    refs = [a["ref"] for a in (await api.get("/api/agents")).json()]
    assert "fake@admin" not in refs
    resp = await api.post(
        "/api/experiments",
        json={"dataset": "finance", "agent": "fake@admin", "capabilities": CAPS},
    )
    assert resp.status_code == 409


async def test_archived_agents_can_be_restored(api):
    await api.post("/api/workspaces/dev/members", json={"username": "bob", "role": "reviewer"})
    created = await api.post("/api/agents", json={"workspace": "dev", "name": "ouragent"})
    assert created.status_code == 200, created.text
    assert (await api.post("/api/agents/ouragent@dev/archive")).json()["archived"]
    bob = await login_as(api, "bob", "password")
    assert (await bob.post("/api/agents/ouragent@dev/restore")).status_code == 403
    restored = await api.post("/api/agents/ouragent@dev/restore")
    assert restored.status_code == 200 and restored.json()["archived"] is False
    refs = [a["ref"] for a in (await api.get("/api/agents")).json()]
    assert "ouragent@dev" in refs
    assert (await api.post("/api/agents/ghost@dev/restore")).status_code == 404
    await bob.aclose()


async def test_unknown_agent_is_404_with_a_hint(api):
    resp = await api.get("/api/agents/ghost@dev")
    assert resp.status_code == 404
    assert "create it with evalkit agents create" in resp.json()["detail"]


async def test_invalid_settings_are_refused(api):
    negative = {"workspace": "dev", "name": "neg", "settings": {"run_timeout_seconds": -1}}
    resp = await api.post("/api/agents", json=negative)
    assert resp.status_code == 422 and "run_timeout_seconds" in resp.text
    provides = {
        "workspace": "dev",
        "name": "harness",
        "harness_url": "http://h",
        "settings": {"provides": {"thinking": True}},
    }
    resp = await api.post("/api/agents", json=provides)
    assert resp.status_code == 400 and "provides" in resp.json()["detail"]


async def test_writers_set_shared_agent_urls_and_keys(api):
    await api.post("/api/workspaces/dev/members", json={"username": "bob", "role": "writer"})
    bob = await login_as(api, "bob", "password")
    with_url = {
        "workspace": "dev",
        "name": "ouragent",
        "harness_url": "http://ouragent",
        "api_key": "secret",
    }
    created = await bob.post("/api/agents", json=with_url)
    assert created.status_code == 200, created.text
    assert created.json()["has_api_key"] is True and "secret" not in created.text
    assert "secret" not in (await api.get("/api/agents/ouragent@dev")).text
    same = await bob.patch(
        "/api/agents/ouragent@dev", json={"harness_url": "http://ouragent", "repo_url": "https://r"}
    )
    assert same.status_code == 200 and same.json()["has_api_key"] is True
    moved = await bob.patch("/api/agents/ouragent@dev", json={"harness_url": "http://moved"})
    assert moved.status_code == 200 and moved.json()["has_api_key"] is False
    rekeyed = await bob.patch(
        "/api/agents/ouragent@dev", json={"harness_url": "http://third", "api_key": "k2"}
    )
    assert rekeyed.json()["has_api_key"] is True
    cleared = await bob.patch("/api/agents/ouragent@dev", json={"api_key": None})
    assert cleared.json()["has_api_key"] is False
    own = await bob.post(
        "/api/agents", json={"workspace": "bob", "name": "mine", "harness_url": "http://h"}
    )
    assert own.status_code == 200
    personal_key = await bob.patch("/api/agents/mine@bob", json={"api_key": "k"})
    assert personal_key.status_code == 400 and "evalkit.yaml" in personal_key.json()["detail"]
    await bob.aclose()
