import io
import json
import zipfile

import httpx
import pytest

from evalkit.core import pull as pull_core
from evalkit.core.harness import HarnessClient
from tests.api.native_api import api_env
from tests.api.test_runs_browser import (
    TRAJECTORY,
    create,
    otel_agent_with_linked_session,
    put_artifact,
    record,
)
from tests.fake_harness import FakeHarness
from tests.native import wait_for


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        yield client, env


def unzip(resp) -> dict[str, bytes]:
    assert resp.status_code == 200, resp.text
    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


async def test_pull_eval_run_by_experiment_carries_case_and_evaluations(api):
    client, _ = api
    exp = await create(client)
    key = f"trajectories/{exp['agent_id']}/sess-9.json"
    await client.put(
        f"/api/experiments/{exp['id']}/blob/{key}", content=json.dumps(TRAJECTORY).encode()
    )
    await record(
        client,
        exp,
        trajectory_key=key,
        trajectory_status="valid",
        metrics={"artifacts": [{"path": "outputs/r.md"}]},
    )
    await put_artifact(client, exp, "sess-9", "outputs/r.md", b"# r")
    pull = "/api/agents/fake@admin/runs/pull"
    files = unzip(await client.post(pull, json={"experiment_id": exp["id"]}))
    index = json.loads(files["index.json"])
    assert index["total_matching"] == 1 and index["agent"] == "fake@admin"
    (row,) = index["runs"]
    assert row["run_id"] == "sess-9" and row["source"] == "eval" and row["case_id"] == "case-2"
    case = json.loads(files["runs/sess-9/case.json"])
    assert case["case_id"] == "case-2" and len(case["content_hash"]) == 16
    assert json.loads(files["runs/sess-9/evaluations.json"]) == []
    assert "runs/sess-9/trajectory.json" in files
    assert not any("/artifacts/" in name for name in files)


async def test_pull_app_sessions_of_an_otel_agent(api):
    client, _ = api
    await otel_agent_with_linked_session(client)
    files = unzip(await client.post("/api/agents/sdk@admin/runs/pull", json={"source": "app"}))
    index = json.loads(files["index.json"])
    assert [r["run_id"] for r in index["runs"]] == ["s-app"]
    assert json.loads(files["runs/s-app/run.json"])["source"] == "app"
    assert "runs/s-app/trajectory.json" in files
    assert "runs/s-app/case.json" not in files


async def test_pull_by_run_ids_marks_a_missing_run_instead_of_failing(api):
    client, _ = api
    await otel_agent_with_linked_session(client)
    files = unzip(
        await client.post("/api/agents/sdk@admin/runs/pull", json={"run_ids": ["s-app", "gone"]})
    )
    index = json.loads(files["index.json"])
    rows = {r["run_id"]: r for r in index["runs"]}
    assert rows["s-app"]["pull_error"] is None
    assert rows["gone"]["pull_error"] == "no spans for this session"
    assert rows["gone"]["error"] is None
    assert "runs/gone/trajectory.json" not in files


async def test_pull_refuses_more_than_100(api):
    client, _ = api
    base = "/api/agents/fake@admin/runs/pull"
    assert (await client.post(base, json={"limit": 101})).status_code == 422
    ids = [f"r{i}" for i in range(101)]
    assert (await client.post(base, json={"run_ids": ids})).status_code == 422


async def test_pull_experiment_of_another_agent_is_404(api):
    client, _ = api
    exp = await create(client)
    resp = await client.post("/api/agents/fake@alice/runs/pull", json={"experiment_id": exp["id"]})
    assert resp.status_code in (403, 404)


async def test_pull_harness_app_run_of_a_shared_agent(tmp_path, stub_judges, session_factory):
    harness = FakeHarness()
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        body = {"dataset": "finance@canonical", "agent": "fake@canonical"}
        body["selection"] = {"case_ids": ["case-1"]}
        exp = (await client.post("/api/experiments", json=body)).json()
        assert (await wait_for(client, exp["id"]))["status"] == "completed"
        harness.runs["sess-extra"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
        files = unzip(
            await client.post("/api/agents/fake@canonical/runs/pull", json={"source": "app"})
        )
        index = json.loads(files["index.json"])
        assert [r["run_id"] for r in index["runs"]] == ["sess-extra"]
        assert "runs/sess-extra/trajectory.json" in files


async def test_pull_app_run_of_a_shared_agent_leaves_out_workspace_files(
    tmp_path, stub_judges, session_factory
):
    harness = FakeHarness(workspace_files={"outputs/a.md": b"a"})
    harness.runs["app-1"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    async with api_env(tmp_path, session_factory, harness) as (client, env):
        pull = "/api/agents/fake@canonical/runs/pull"
        files = unzip(await client.post(pull, json={"run_ids": ["app-1"]}))
        assert "runs/app-1/trajectory.json" in files
        assert not any("/artifacts/" in name for name in files)
        assert await env.deps.blob_store.list_objects("artifacts/") == []


async def test_pull_carries_the_harness_scan_note(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    monkeypatch.setattr(pull_core, "HARNESS_PAGE", 1)
    monkeypatch.setattr(pull_core, "MAX_HARNESS_SCAN", 1)
    harness = FakeHarness()
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        body = {"dataset": "finance@canonical", "agent": "fake@canonical"}
        exp = (await client.post("/api/experiments", json=body)).json()
        assert (await wait_for(client, exp["id"]))["status"] == "completed"
        files = unzip(
            await client.post("/api/agents/fake@canonical/runs/pull", json={"source": "app"})
        )
        index = json.loads(files["index.json"])
        assert index["runs"] == [] and index["note"] == pull_core.SCAN_NOTE


async def test_pull_experiment_survives_a_down_harness(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    harness = FakeHarness()
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        body = {"dataset": "finance@canonical", "agent": "fake@canonical"}
        exp = (await client.post("/api/experiments", json=body)).json()
        assert (await wait_for(client, exp["id"]))["status"] == "completed"

        async def down(self):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(HarnessClient, "capabilities", down)
        files = unzip(
            await client.post(
                "/api/agents/fake@canonical/runs/pull", json={"experiment_id": exp["id"]}
            )
        )
        index = json.loads(files["index.json"])
        assert len(index["runs"]) == 2
        assert [row["pull_error"] for row in index["runs"]] == [None, None], index["runs"]


async def test_pull_marks_an_unsafe_harness_run_id_instead_of_failing(
    tmp_path, stub_judges, session_factory
):
    harness = FakeHarness()
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        harness.runs["a/b"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
        resp = await client.post("/api/agents/fake@canonical/runs/pull", json={"source": "app"})
        assert resp.status_code == 200
        index = json.loads(unzip(resp)["index.json"])
        [run] = [r for r in index["runs"] if r["run_id"] == "a/b"]
        assert "unsafe run id" in run["pull_error"]
