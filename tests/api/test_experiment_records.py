import json

import pytest

from evalkit.contracts.enums import DatasetKind, SyncSource
from evalkit.db.workspaces import WorkspaceStore
from evalkit.store.base import IncomingOverlay
from tests.fake_agents import reference_mapping
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent
from tests.native import login_as, seed_env, serve

CAPS = FakeHarness().capabilities
HOLDER = "cli:admin@laptop:1"
FINANCE = "finance@canonical"


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    await seed_agent(session_factory, "fake", "admin")
    await seed_agent(session_factory, "fake", "alice")
    async with serve(env, tmp_path) as client:
        yield client, env


async def create(client, agent="fake@admin", **extra):
    body = {"dataset": FINANCE, "agent": agent, "capabilities": CAPS, "holder": HOLDER, **extra}
    return await client.post("/api/experiments", json=body)


async def overlay(env, agents: list[str]) -> None:
    await env.store.sync(
        workspace=await WorkspaceStore(env.session_factory).get_by_name("canonical"),
        dataset="fake-cases",
        kind=DatasetKind.SUITE,
        incoming=[],
        overlays=[
            IncomingOverlay(
                target_case_id="case-1",
                agents=agents,
                tags=[],
                evaluators={"custom": {"probe": {"x": 1}}},
            )
        ],
        full=False,
        actor_id=env.user_ids["admin"],
        source=SyncSource.GIT,
        detail={},
    )


async def test_workspace_agent_creates_record_without_running(api):
    client, env = api
    resp = await create(
        client, cases={"sha": "abc", "branch": "b", "dirty": False}, evalkit_version="0.4.0"
    )
    assert resp.status_code == 200, resp.text
    exp_id = resp.json()["id"]
    assert await env.details(exp_id) == []
    summary = (await client.get(f"/api/experiments/{exp_id}")).json()
    assert summary["agent"] == "fake@admin" and summary["workspace"] == "admin"
    assert summary["created_by"] == "admin"
    assert summary["status"] == "running" and summary["lease_holder"] == HOLDER
    assert summary["config"]["revision"]["sha"] == "abc"
    assert summary["config"]["snapshot"]["evalkit_version"] == "0.4.0"
    assert summary["progress"] == {"total": 2, "ran": 0, "evaluated": 0}
    assert summary["can_change"] is True and summary["server_may_drive"] is False
    agents = {a["slug"]: a for a in (await client.get("/api/agents")).json()}
    assert agents["fake@admin"]["name"] == "fake" and agents["fake@admin"]["workspace"] == "admin"


async def test_workspace_agent_requires_capabilities_holder_and_own_workspace(api):
    client, _ = api
    resp = await client.post("/api/experiments", json={"dataset": FINANCE, "agent": "fake@admin"})
    assert resp.status_code == 400
    no_holder = {"dataset": FINANCE, "agent": "fake@admin", "capabilities": CAPS}
    assert (await client.post("/api/experiments", json=no_holder)).status_code == 400
    resp = await create(client, agent="fake@alice")
    assert resp.status_code == 403


async def test_canonical_agent_rejects_capabilities_and_unknown_names(api):
    client, _ = api
    resp = await create(client, agent="fake@canonical")
    assert resp.status_code == 400
    resp = await client.post(
        "/api/experiments", json={"dataset": FINANCE, "agent": "nope@canonical"}
    )
    assert resp.status_code == 404


async def test_record_run_evaluations_patch_lease_and_blob_round_trip(api):
    client, env = api
    created = (await create(client)).json()
    exp_id, agent_id = created["id"], created["agent_id"]
    cases = (await client.get(f"/api/experiments/{exp_id}/cases")).json()
    assert {c["case_id"] for c in cases} == {"case-1", "case-2"}

    key = f"trajectories/{agent_id}/sess-1.json"
    put = await client.put(f"/api/experiments/{exp_id}/blob/{key}", content=b"{}")
    assert put.status_code == 200, put.text
    assert (await client.head(f"/api/experiments/{exp_id}/blob/{key}")).status_code == 200
    assert (await client.get(f"/api/experiments/{exp_id}/blob/{key}")).content == b"{}"
    missing = await client.head(f"/api/experiments/{exp_id}/blob/trajectories/{agent_id}/none.json")
    assert missing.status_code == 404
    bad = await client.put(f"/api/experiments/{exp_id}/blob/fixtures/x.csv", content=b"x")
    assert bad.status_code == 400
    other_agent = await client.put(
        f"/api/experiments/{exp_id}/blob/trajectories/{agent_id + 100}/x.json", content=b"x"
    )
    assert other_agent.status_code == 400
    listing = (
        await client.get(
            f"/api/experiments/{exp_id}/blob", params={"prefix": f"trajectories/{agent_id}/"}
        )
    ).json()
    assert listing["objects"] == [[key, 2]]

    run = {
        "case_id": "case-1",
        "repetition": 1,
        "run": {
            "harness_run_id": "sess-1",
            "status": "completed",
            "answer": "42",
            "started_at": "2026-10-05T00:00:00Z",
            "ended_at": "2026-10-05T00:00:01Z",
            "trajectory_key": key,
            "trajectory_status": "valid",
            "metrics": {"duration_ms": 1000},
            "title": "q1",
        },
        "holder": HOLDER,
    }
    ref = (await client.post(f"/api/experiments/{exp_id}/runs", json=run)).json()
    unpinned = await client.post(
        f"/api/experiments/{exp_id}/runs", json={**run, "case_id": "case-9"}
    )
    assert unpinned.status_code == 400 and "not pinned" in unpinned.json()["detail"]
    foreign_key = {**run["run"], "harness_run_id": "sess-x", "trajectory_key": "fixtures/x"}
    bad_key = await client.post(
        f"/api/experiments/{exp_id}/runs", json={**run, "case_id": "case-2", "run": foreign_key}
    )
    assert bad_key.status_code == 400
    someone_elses = {**run["run"], "harness_run_id": "sess-y", "trajectory_key": key}
    borrowed = await client.post(
        f"/api/experiments/{exp_id}/runs", json={**run, "case_id": "case-2", "run": someone_elses}
    )
    assert borrowed.status_code == 400
    assert f"trajectories/{agent_id}/sess-y.json" in borrowed.json()["detail"]
    reused = await client.put(f"/api/experiments/{exp_id}/blob/{key}", content=b"{}")
    assert reused.status_code == 409 and "already used" in reused.json()["detail"]
    pending = (await client.get(f"/api/experiments/{exp_id}/pending")).json()
    assert pending == [{"case_id": "case-2", "repetition": 1}]
    rows = [
        {
            "producer": "answer_rubrics",
            "name": "answer_rubric_score",
            "kind": "score",
            "source": "builtin",
            "value": 1.0,
        }
    ]
    stale = await client.post(
        f"/api/experiment-runs/{ref['experiment_run_id']}/evaluations",
        json={"rows": rows, "holder": "cli:admin@old-laptop:9"},
    )
    assert stale.status_code == 409
    resp = await client.post(
        f"/api/experiment-runs/{ref['experiment_run_id']}/evaluations",
        json={"rows": rows, "holder": HOLDER},
    )
    assert resp.status_code == 200, resp.text
    unknown = await client.post(
        "/api/experiment-runs/99999/evaluations", json={"rows": rows, "holder": HOLDER}
    )
    assert unknown.status_code == 404
    to_score = (await client.get(f"/api/experiments/{exp_id}/runs-to-score")).json()
    assert [r["done"] for r in to_score] == [["answer_rubric_score"]]

    assert (await client.post(f"/api/experiments/{exp_id}/lease", json={"holder": HOLDER})).json()[
        "renewed"
    ] is True
    stranger = await client.post(f"/api/experiments/{exp_id}/lease", json={"holder": "other"})
    assert stranger.json() == {"renewed": False}

    assert (
        await client.patch(f"/api/experiments/{exp_id}", json={"model": "m1"})
    ).status_code == 200
    stolen = await client.patch(
        f"/api/experiments/{exp_id}", json={"status": "evaluating", "holder": "other"}
    )
    assert stolen.status_code == 409
    for status in ("evaluating", "completed"):
        patched = await client.patch(
            f"/api/experiments/{exp_id}", json={"status": status, "holder": HOLDER}
        )
        assert patched.status_code == 200, patched.text
    late = await client.post(f"/api/experiments/{exp_id}/runs", json={**run, "case_id": "case-2"})
    assert late.status_code == 409
    exp = await env.experiment(exp_id)
    assert exp.config.model == "m1" and exp.status == "completed"
    [detail] = await env.details(exp_id)
    assert [e.name for e in detail.evaluations] == ["answer_rubric_score"]

    runs = (await client.get("/api/agents/fake@admin/runs")).json()
    assert runs["total"] == 1 and runs["runs"][0]["run_id"] == "sess-1"
    assert runs["runs"][0]["title"] == "q1"
    assert runs["runs"][0]["experiment_id"] == exp_id


async def test_uploads_require_owner(api):
    client, _ = api
    created = (await create(client)).json()
    exp_id, agent_id = created["id"], created["agent_id"]
    alice = await login_as(client, "alice")
    denied = await alice.patch(
        f"/api/experiments/{exp_id}", json={"status": "failed", "holder": HOLDER}
    )
    assert denied.status_code == 403
    denied = await alice.put(
        f"/api/experiments/{exp_id}/blob/artifacts/{agent_id}/r/a.txt", content=b"x"
    )
    assert denied.status_code == 403
    looked = await alice.get(f"/api/experiments/{exp_id}")
    assert looked.status_code == 200 and looked.json()["can_change"] is False


async def test_blob_writes_only_reach_this_experiments_runs(api):
    client, _ = api
    first = (await create(client)).json()
    second = (await create(client)).json()
    agent_id = first["agent_id"]
    run = {
        "case_id": "case-1",
        "repetition": 1,
        "run": {
            "harness_run_id": "team-run",
            "status": "completed",
            "started_at": "2026-10-05T00:00:00Z",
        },
        "holder": HOLDER,
    }
    assert (await client.post(f"/api/experiments/{first['id']}/runs", json=run)).status_code == 200
    key = f"trajectories/{agent_id}/team-run.json"
    own = await client.put(f"/api/experiments/{first['id']}/blob/{key}", content=b"{}")
    assert own.status_code == 200
    hijack = await client.put(f"/api/experiments/{second['id']}/blob/{key}", content=b"{}")
    assert hijack.status_code == 403
    artifact = f"artifacts/{agent_id}/team-run/report.csv"
    hijack = await client.put(f"/api/experiments/{second['id']}/blob/{artifact}", content=b"x")
    assert hijack.status_code == 403
    fresh = f"trajectories/{agent_id}/not-recorded-yet.json"
    assert (
        await client.put(f"/api/experiments/{second['id']}/blob/{fresh}", content=b"{}")
    ).status_code == 200


async def test_malformed_agent_slug_is_400(api):
    client, _ = api
    resp = await client.post("/api/experiments", json={"dataset": FINANCE, "agent": "a@"})
    assert resp.status_code == 400
    assert (await client.get("/api/agents/a@/runs")).status_code == 400


async def test_pinned_cases_carry_the_agents_effective_evaluators(api):
    client, env = api
    await overlay(env, ["fake"])
    exp_id = (await create(client)).json()["id"]
    cases = (await client.get(f"/api/experiments/{exp_id}/cases")).json()
    case_1 = next(c for c in cases if c["case_id"] == "case-1")
    assert case_1["effective_evaluators"]["custom"]["probe"] == {"x": 1}
    assert "probe" not in case_1["evaluators"]["custom"]


async def test_pinned_cases_skip_overlays_for_other_agents(api):
    client, env = api
    await overlay(env, ["other"])
    exp_id = (await create(client)).json()["id"]
    cases = (await client.get(f"/api/experiments/{exp_id}/cases")).json()
    case_1 = next(c for c in cases if c["case_id"] == "case-1")
    assert "probe" not in (case_1["effective_evaluators"].get("custom") or {})


async def test_sdk_experiment_takes_trajectory_settings_from_the_agent(api):
    client, _ = api
    created = await client.post(
        "/api/agents",
        json={
            "workspace": "admin",
            "name": "sdk",
            "trajectory_source": "otel",
            "settings": {"trajectory_wait_seconds": 30},
            "mapping": reference_mapping(),
        },
    )
    assert created.status_code == 200, created.text
    body = {
        "dataset": FINANCE,
        "agent": "sdk@admin",
        "capabilities": {**CAPS, "contract_version": "sdk"},
        "code_evaluators": ["citation_check"],
        "holder": HOLDER,
    }
    resp = await client.post("/api/experiments", json=body)
    assert resp.status_code == 200, resp.text
    snapshot = resp.json()["config"]["snapshot"]
    assert snapshot["trajectory_source"] == "otel"
    assert snapshot["code_evaluators"] == ["citation_check"]
    assert snapshot["trajectory_wait_seconds"] == 30
    agents = {a["ref"]: a for a in (await client.get("/api/agents")).json()}
    assert agents["sdk@admin"]["trajectory_source"] == "otel"
    assert agents["sdk@admin"]["driver"] == "sdk"


async def test_run_listing_reports_the_refetched_trajectory_status(api):
    client, _ = api
    created = (await create(client)).json()
    exp_id, agent_id = created["id"], created["agent_id"]
    run = {
        "case_id": "case-1",
        "repetition": 1,
        "run": {
            "harness_run_id": "late-1",
            "status": "completed",
            "answer": "42",
            "started_at": "2026-10-05T00:00:00Z",
            "ended_at": "2026-10-05T00:00:01Z",
            "trajectory_status": "unavailable",
        },
        "holder": HOLDER,
    }
    ref = (await client.post(f"/api/experiments/{exp_id}/runs", json=run)).json()
    run_id = ref["experiment_run_id"]
    listed = (await client.get(f"/api/experiments/{exp_id}/runs")).json()
    assert listed[0]["agent_run"]["trajectory_status"] == "unavailable"
    assert (
        await client.get(f"/api/experiments/{exp_id}/runs/{run_id}/trajectory")
    ).status_code == 404
    doc = {"schema_version": "ATIF-v1.8", "steps": [], "extra": {"trajectory_status": "valid"}}
    key = f"trajectories/{agent_id}/late-1.json"
    await client.put(f"/api/experiments/{exp_id}/blob/{key}", content=json.dumps(doc).encode())
    listed = (await client.get(f"/api/experiments/{exp_id}/runs")).json()
    assert listed[0]["agent_run"]["trajectory_status"] == "valid"
    served = await client.get(f"/api/experiments/{exp_id}/runs/{run_id}/trajectory")
    assert served.status_code == 200 and served.json()["extra"]["trajectory_status"] == "valid"
