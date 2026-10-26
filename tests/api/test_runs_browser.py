import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi import HTTPException

from evalkit.api import runs
from evalkit.core import pull as pull_core
from evalkit.core.blob import artifact_key
from evalkit.core.harness import HarnessClient
from tests.api.native_api import CAPS, api_env
from tests.api.test_otel import PROTO, otlp_payload
from tests.fake_agents import reference_mapping
from tests.fake_harness import FakeHarness
from tests.native import wait_for

TRAJECTORY = json.loads((Path(__file__).parent.parent / "data" / "atif_full.json").read_text())
HOLDER = "cli:admin@laptop:1"


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        yield client, env


async def create(client, agent="fake@admin") -> dict:
    body = {"dataset": "finance@canonical", "agent": agent, "capabilities": CAPS, "holder": HOLDER}
    resp = await client.post("/api/experiments", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def record(client, exp: dict, harness_run_id: str = "sess-9", **run) -> None:
    body = {
        "case_id": "case-2",
        "repetition": 1,
        "run": {
            "harness_run_id": harness_run_id,
            "status": "completed",
            "started_at": "2026-10-05T00:00:00Z",
            "ended_at": "2026-10-05T00:00:01Z",
            **run,
        },
        "holder": HOLDER,
    }
    resp = await client.post(f"/api/experiments/{exp['id']}/runs", json=body)
    assert resp.status_code == 200, resp.text


async def put_artifact(client, exp: dict, run_id: str, path: str, data: bytes) -> None:
    key = f"artifacts/{exp['agent_id']}/{run_id}/{path}"
    resp = await client.put(f"/api/experiments/{exp['id']}/blob/{key}", content=data)
    assert resp.status_code == 200, resp.text


async def test_agents_list_has_shared_agents_first(api):
    client, _ = api
    agents = (await client.get("/api/agents")).json()
    assert [a["ref"] for a in agents] == [
        "fake@canonical",
        "fake@admin",
        "fake@akshat",
        "fake@alice",
    ]
    assert agents[0]["slug"] == "fake@canonical" and agents[0]["driver"] == "server"
    assert agents[1]["driver"] == "cli"


async def test_workspace_agent_runs_come_from_blob_and_db(api):
    client, _ = api
    exp = await create(client)
    key = f"trajectories/{exp['agent_id']}/sess-9.json"
    await client.put(
        f"/api/experiments/{exp['id']}/blob/{key}", content=json.dumps(TRAJECTORY).encode()
    )
    await record(client, exp, trajectory_key=key, trajectory_status="valid", title="q2")
    await put_artifact(client, exp, "sess-9", "outputs/r.md", b"# r")

    listed = (await client.get("/api/agents/fake@admin/runs", params={"query": "q2"})).json()
    assert listed["total"] == 1 and listed["runs"][0]["run_id"] == "sess-9"
    assert listed["runs"][0]["experiment_id"] == exp["id"]
    assert listed["runs"][0]["case_id"] == "case-2"
    by_case = (await client.get("/api/agents/fake@admin/runs", params={"query": "case-2"})).json()
    assert [r["run_id"] for r in by_case["runs"]] == ["sess-9"]
    body = (await client.get("/api/agents/fake@admin/runs/sess-9/trajectory")).json()
    assert body["schema_version"] == TRAJECTORY["schema_version"]
    files = (await client.get("/api/agents/fake@admin/runs/sess-9/files")).json()
    assert files == {
        "files": [{"path": "outputs/r.md", "size": 3, "content_type": None, "stored": True}]
    }
    data = await client.get("/api/agents/fake@admin/runs/sess-9/files/outputs/r.md")
    assert data.content == b"# r" and data.headers["content-length"] == "3"
    assert (await client.get("/api/agents/fake@admin/runs/nope/trajectory")).status_code == 404


async def test_canonical_agent_runs_stay_live(api):
    client, _ = api
    listed = (await client.get("/api/agents/fake@canonical/runs")).json()
    assert listed == {"runs": [], "total": 0}
    assert (await client.get("/api/agents/nope@canonical/runs")).status_code == 404


async def test_file_download_header_and_folder_zip_for_workspace_agent(api):
    client, _ = api
    exp = await create(client)
    await record(client, exp)
    files = {"outputs/r.md": b"# r", "outputs/sub/t.txt": b"t", "notes.txt": b"n"}
    for path, data in files.items():
        await put_artifact(client, exp, "sess-9", path, data)
    base = "/api/agents/fake@admin/runs/sess-9"

    named = await client.get(f"{base}/files/outputs/r.md", params={"download": 1})
    assert named.headers["content-disposition"] == (
        "attachment; filename=\"r.md\"; filename*=UTF-8''r.md"
    )
    assert "content-disposition" not in (await client.get(f"{base}/files/outputs/r.md")).headers

    folder = await client.get(f"{base}/zip", params={"path": "outputs"})
    assert folder.headers["content-type"] == "application/zip"
    assert folder.headers["content-disposition"].startswith('attachment; filename="outputs.zip"')
    with zipfile.ZipFile(io.BytesIO(folder.content)) as archive:
        assert sorted(archive.namelist()) == ["outputs/r.md", "outputs/sub/t.txt"]
        assert archive.read("outputs/sub/t.txt") == b"t"

    whole = await client.get(f"{base}/zip")
    assert whole.headers["content-disposition"].startswith('attachment; filename="sess-9.zip"')
    with zipfile.ZipFile(io.BytesIO(whole.content)) as archive:
        assert sorted(archive.namelist()) == [
            "sess-9/notes.txt",
            "sess-9/outputs/r.md",
            "sess-9/outputs/sub/t.txt",
        ]

    assert (await client.get(f"{base}/zip", params={"path": "nope"})).status_code == 404


async def test_non_ascii_filenames_and_oversized_files(api, monkeypatch):
    client, _ = api
    exp = await create(client)
    await record(client, exp)
    name = 'रिपोर्ट "q".md'
    await put_artifact(client, exp, "sess-9", quote(name), b"x")
    base = "/api/agents/fake@admin/runs/sess-9"

    named = await client.get(f"{base}/files/{quote(name)}", params={"download": 1})
    assert named.status_code == 200
    assert named.headers["content-disposition"] == (
        f"attachment; filename=\"q_.md\"; filename*=UTF-8''{quote(name, safe='')}"
    )

    monkeypatch.setattr(runs, "MAX_FILE_BYTES", 0)
    too_big = await client.get(f"{base}/zip")
    assert too_big.status_code == 413
    assert too_big.json()["detail"].startswith(name)


async def test_folder_zip_for_canonical_agent_proxies_the_harness(
    tmp_path, stub_judges, session_factory
):
    harness = FakeHarness(workspace_files={"outputs/a.md": b"a", "c.txt": b"c"})
    harness.runs["sess-1"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        base = "/api/agents/fake@canonical/runs/sess-1/zip"
        resp = await client.get(base, params={"path": "outputs"})
        assert resp.status_code == 200
        with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
            assert archive.namelist() == ["outputs/a.md"]
        assert (await client.get(base, params={"path": "nope"})).status_code == 404


BIG = bytes(range(256)) * (3 * 4096 + 7)


class _FailingFiles(dict):
    def __getitem__(self, key):
        raise HTTPException(status_code=503)


async def test_large_harness_file_streams_byte_exact(tmp_path, stub_judges, session_factory):
    harness = FakeHarness(workspace_files={"out/big.bin": BIG, "out/s.txt": b"s"})
    harness.runs["sess-1"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        base = "/api/agents/fake@canonical/runs/sess-1"
        single = await client.get(f"{base}/files/out/big.bin", params={"download": 1})
        assert single.status_code == 200 and single.content == BIG
        assert single.headers["content-length"] == str(len(BIG))
        assert single.headers["content-disposition"].startswith('attachment; filename="big.bin"')
        folder = await client.get(f"{base}/zip", params={"path": "out"})
        with zipfile.ZipFile(io.BytesIO(folder.content)) as archive:
            assert archive.read("out/big.bin") == BIG
            assert archive.read("out/s.txt") == b"s"


async def test_harness_errors_on_a_single_file_keep_their_status(
    tmp_path, stub_judges, session_factory
):
    harness = FakeHarness(workspace_files={"a.md": b"a"})
    harness.runs["sess-1"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        base = "/api/agents/fake@canonical/runs/sess-1/files"
        missing = await client.get(f"{base}/nope.md")
        assert missing.status_code == 404 and missing.json()["detail"] == runs.NOT_ON_HARNESS
        harness.workspace_files = _FailingFiles({"a.md": b"a"})
        failed = await client.get(f"{base}/a.md")
        assert failed.status_code == 503 and failed.json()["detail"] == "harness error"


async def test_single_file_closes_the_harness_client_on_any_error(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    harness = FakeHarness(workspace_files={"a.md": b"a"})
    harness.runs["sess-1"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    closed = []
    original = HarnessClient.aclose

    async def refuse(self, run_id, path):
        raise httpx.ConnectError("harness down")

    async def tracked(self):
        closed.append(True)
        await original(self)

    monkeypatch.setattr(HarnessClient, "stream_file", refuse)
    monkeypatch.setattr(HarnessClient, "aclose", tracked)
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        with pytest.raises(httpx.ConnectError):
            await client.get("/api/agents/fake@canonical/runs/sess-1/files/a.md")
    assert closed == [True]


async def test_large_stored_file_zips_byte_exact(api):
    client, _ = api
    exp = await create(client)
    await record(client, exp)
    await put_artifact(client, exp, "sess-9", "out/big.bin", BIG)
    folder = await client.get("/api/agents/fake@admin/runs/sess-9/zip")
    with zipfile.ZipFile(io.BytesIO(folder.content)) as archive:
        assert archive.read("sess-9/out/big.bin") == BIG


def test_zip_entries_ignore_a_leading_slash():
    listing = {"/outputs/a.md": 1, "/c.txt": 1}
    assert runs._zip_entries(listing, "outputs", "sess-1") == (
        "outputs",
        [("outputs/a.md", "/outputs/a.md")],
    )
    assert runs._zip_entries(listing, "", "sess-1")[1] == [
        ("sess-1/c.txt", "/c.txt"),
        ("sess-1/outputs/a.md", "/outputs/a.md"),
    ]


async def test_zip_aborts_when_the_harness_fails_mid_stream(
    tmp_path, stub_judges, monkeypatch, session_factory
):
    harness = FakeHarness(workspace_files={"a.md": b"a", "b.md": b"b"})
    harness.runs["sess-1"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    original = HarnessClient.stream_file

    async def flaky(self, run_id, path):
        if path == "b.md":
            request = httpx.Request("GET", "http://fake/b.md")
            response = httpx.Response(502, request=request)
            raise httpx.HTTPStatusError("gone", request=request, response=response)
        return await original(self, run_id, path)

    monkeypatch.setattr(HarnessClient, "stream_file", flaky)
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        with pytest.raises(httpx.HTTPStatusError):
            await client.get("/api/agents/fake@canonical/runs/sess-1/zip")


async def test_otel_agent_runs_list_sessions_and_convert_on_demand(api):
    client, _ = api
    created = await client.post(
        "/api/agents",
        json={
            "workspace": "admin",
            "name": "sdk",
            "trajectory_source": "otel",
            "mapping": reference_mapping(),
        },
    )
    assert created.status_code == 200, created.text
    headers = {**PROTO, "X-Evalkit-Agent": "sdk"}
    await client.post(
        "/api/otel/v1/traces", content=otlp_payload(session_id="s-1"), headers=headers
    )
    await client.post(
        "/api/otel/v1/traces", content=otlp_payload(session_id="s-2"), headers=headers
    )
    listed = (await client.get("/api/agents/sdk@admin/runs")).json()
    assert listed["total"] == 2 and {r["run_id"] for r in listed["runs"]} == {"s-1", "s-2"}
    assert listed["runs"][0]["title"] == "evalkit.run"
    filtered = (await client.get("/api/agents/sdk@admin/runs", params={"query": "s-2"})).json()
    assert [r["run_id"] for r in filtered["runs"]] == ["s-2"]
    doc = (await client.get("/api/agents/sdk@admin/runs/s-1/trajectory")).json()
    assert doc["schema_version"] == "ATIF-v1.8" and doc["extra"]["trajectory_source"] == "otel"
    assert (await client.get("/api/agents/sdk@admin/runs/nope/trajectory")).status_code == 404
    none = (await client.get("/api/agents/sdk@admin/runs", params={"no_session": "true"})).json()
    assert none == {"runs": [], "total": 0}


@pytest.fixture
async def shared(tmp_path, stub_judges, session_factory):
    harness = FakeHarness()
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        body = {"dataset": "finance@canonical", "agent": "fake@canonical"}
        body["selection"] = {"case_ids": ["case-1"]}
        exp = (await client.post("/api/experiments", json=body)).json()
        assert (await wait_for(client, exp["id"]))["status"] == "completed"
        harness.runs["sess-extra"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
        yield client, harness, exp


async def test_shared_agent_merges_db_runs_with_the_harness_list(shared):
    client, _, exp = shared
    listed = (await client.get("/api/agents/fake@canonical/runs")).json()
    rows = {r["run_id"]: r for r in listed["runs"]}
    assert set(rows) == {"sess-1", "sess-extra"}
    assert rows["sess-1"]["stored"] is True
    assert rows["sess-1"]["experiment_id"] == exp["id"]
    assert rows["sess-1"]["case_id"] == "case-1"
    assert rows["sess-extra"]["stored"] is False
    assert "harness_note" not in listed


async def test_search_matching_only_the_harness_lists_the_harness_row(shared):
    client, _, _ = shared
    listed = (await client.get("/api/agents/fake@canonical/runs", params={"query": "zzz"})).json()
    rows = {r["run_id"]: r for r in listed["runs"]}
    assert rows["sess-extra"]["stored"] is False
    assert all(not r["stored"] for r in rows.values())


async def test_harness_down_still_lists_db_runs_with_a_note(shared, monkeypatch):
    client, _, _ = shared

    async def down(self, params):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(HarnessClient, "list_runs", down)
    listed = (await client.get("/api/agents/fake@canonical/runs")).json()
    assert [r["run_id"] for r in listed["runs"]] == ["sess-1"]
    assert listed["harness_note"] == "harness unreachable"


async def test_shared_agent_trajectory_comes_from_the_stored_blob(shared):
    client, harness, _ = shared
    harness.trajectory_doc = {"not": "atif"}
    doc = (await client.get("/api/agents/fake@canonical/runs/sess-1/trajectory")).json()
    assert doc["schema_version"] == TRAJECTORY["schema_version"]


async def test_stored_files_come_before_the_harness(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, env):
        agent = (await client.get("/api/agents/fake@canonical")).json()
        key = artifact_key(agent["id"], "gone", "outputs/kept.md")
        await env.deps.blob_store.put_object(key, b"kept")
        base = "/api/agents/fake@canonical/runs/gone/files"
        listed = (await client.get(base)).json()["files"]
        assert [entry["path"] for entry in listed] == ["outputs/kept.md"]
        assert (await client.get(f"{base}/outputs/kept.md")).content == b"kept"
        assert (await client.get(f"{base}/outputs/other.md")).status_code == 404


async def test_snapshot_lists_the_workspace_after_the_harness_forgets_it(
    tmp_path, stub_judges, session_factory
):
    files = {"outputs/report.md": b"# report", "scratch/notes.txt": b"n"}
    harness = FakeHarness(workspace_files=files)
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        body = {"dataset": "finance@canonical", "agent": "fake@canonical"}
        body["selection"] = {"case_ids": ["case-1"]}
        exp = (await client.post("/api/experiments", json=body)).json()
        assert (await wait_for(client, exp["id"]))["status"] == "completed"
        base = "/api/agents/fake@canonical/runs/sess-1/files"
        assert (await client.get(f"{base}/scratch/notes.txt")).content == b"n"
        harness.runs.clear()
        listed = {e["path"]: e["stored"] for e in (await client.get(base)).json()["files"]}
        assert listed == {"outputs/report.md": True, "scratch/notes.txt": False}
        assert (await client.get(f"{base}/outputs/report.md")).content == b"# report"
        gone = await client.get(f"{base}/scratch/notes.txt")
        assert gone.status_code == 404 and gone.json()["detail"] == runs.NOT_ON_HARNESS


async def test_files_gone_from_the_harness(shared):
    client, _, _ = shared
    resp = await client.get("/api/agents/fake@canonical/runs/gone/files")
    assert resp.status_code == 404
    assert resp.json()["detail"] == runs.NOT_ON_HARNESS


async def otel_agent_with_linked_session(client) -> dict:
    created = await client.post(
        "/api/agents",
        json={
            "workspace": "admin",
            "name": "sdk",
            "trajectory_source": "otel",
            "mapping": reference_mapping(),
        },
    )
    assert created.status_code == 200, created.text
    headers = {**PROTO, "X-Evalkit-Agent": "sdk"}
    for session_id in ("s-eval", "s-app"):
        await client.post(
            "/api/otel/v1/traces", content=otlp_payload(session_id=session_id), headers=headers
        )
    exp = await create(client, agent="sdk@admin")
    await record(client, exp, harness_run_id="s-eval")
    return exp


async def test_source_filter_splits_otel_sessions_into_app_and_eval(api):
    client, _ = api
    exp = await otel_agent_with_linked_session(client)
    base = "/api/agents/sdk@admin/runs"
    every = {r["run_id"]: r for r in (await client.get(base)).json()["runs"]}
    assert every["s-eval"]["source"] == "eval" and every["s-app"]["source"] == "app"
    evals = (await client.get(base, params={"source": "eval"})).json()
    assert [r["run_id"] for r in evals["runs"]] == ["s-eval"] and evals["total"] == 1
    assert evals["runs"][0]["experiment_id"] == exp["id"]
    apps = (await client.get(base, params={"source": "app"})).json()
    assert [r["run_id"] for r in apps["runs"]] == ["s-app"] and apps["total"] == 1


async def test_time_window_filters_otel_sessions(api):
    client, _ = api
    await otel_agent_with_linked_session(client)
    base = "/api/agents/sdk@admin/runs"
    future = (await client.get(base, params={"since": "2999-01-01T00:00:00Z"})).json()
    past = (await client.get(base, params={"until": "2000-01-01T00:00:00Z"})).json()
    assert future["total"] == 0 and past["total"] == 0
    window = {"since": "2000-01-01T00:00:00Z", "until": "2999-01-01T00:00:00Z"}
    assert (await client.get(base, params=window)).json()["total"] == 2


async def test_time_window_and_source_filter_stored_runs(api):
    client, _ = api
    exp = await create(client)
    await record(client, exp)
    base = "/api/agents/fake@admin/runs"
    inside = {"since": "2026-10-04T00:00:00Z", "until": "2026-10-06T00:00:00Z"}
    assert [r["run_id"] for r in (await client.get(base, params=inside)).json()["runs"]] == [
        "sess-9"
    ]
    later = (await client.get(base, params={"since": "2026-10-06T00:00:00Z"})).json()
    earlier = (await client.get(base, params={"until": "2026-10-05T00:00:00Z"})).json()
    assert later["total"] == 0 and earlier["total"] == 0
    assert (await client.get(base, params={"source": "app"})).json()["total"] == 0
    assert (await client.get(base, params={"source": "eval"})).json()["total"] == 1


async def test_source_filter_on_a_shared_harness_agent(shared):
    client, _, _ = shared
    base = "/api/agents/fake@canonical/runs"
    apps = (await client.get(base, params={"source": "app"})).json()
    assert [r["run_id"] for r in apps["runs"]] == ["sess-extra"]
    assert apps["runs"][0]["source"] == "app"
    evals = (await client.get(base, params={"source": "eval"})).json()
    assert [r["run_id"] for r in evals["runs"]] == ["sess-1"]
    window = {"source": "app", "since": "2026-09-02T00:00:00Z"}
    assert (await client.get(base, params=window)).json()["runs"] == []


async def test_app_runs_past_a_page_of_eval_runs_are_still_listed(
    tmp_path, stub_judges, session_factory
):
    harness = FakeHarness()
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        body = {"dataset": "finance@canonical", "agent": "fake@canonical"}
        exp = (await client.post("/api/experiments", json=body)).json()
        assert (await wait_for(client, exp["id"]))["status"] == "completed"
        harness.runs["app-old"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
        base = "/api/agents/fake@canonical/runs"
        listed = (await client.get(base, params={"source": "app", "limit": 1})).json()
        assert [r["run_id"] for r in listed["runs"]] == ["app-old"] and listed["total"] == 1


async def test_harness_ignoring_the_window_is_paged_past_one_page(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    monkeypatch.setattr(pull_core, "HARNESS_PAGE", 2)
    harness = FakeHarness(ignore_time_filter=True)
    for i in range(3):
        harness.runs[f"newer-{i}"] = {
            "polls": 0,
            "stopped": False,
            "payload": {},
            "model": "m",
            "created_at": datetime(2026, 10, 9, tzinfo=UTC),
        }
    harness.runs["new"] = {
        "polls": 0,
        "stopped": False,
        "payload": {},
        "model": "m",
        "created_at": datetime(2026, 10, 1, tzinfo=UTC),
    }
    for i in range(3):
        harness.runs[f"old-{i}"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        params = {
            "source": "app",
            "since": "2026-09-15T00:00:00Z",
            "until": "2026-10-05T00:00:00Z",
            "limit": 10,
        }
        listed = (await client.get("/api/agents/fake@canonical/runs", params=params)).json()
        assert [r["run_id"] for r in listed["runs"]] == ["new"] and listed["total"] == 1


async def test_runs_sharing_a_session_are_listed_once(tmp_path, stub_judges, session_factory):
    harness = FakeHarness()
    for run_id, session_id in [("msg-3", "chat-a"), ("msg-2", "chat-b"), ("msg-1", "chat-a")]:
        harness.runs[run_id] = {
            "polls": 0,
            "stopped": False,
            "payload": {},
            "model": "m",
            "session_id": session_id,
        }
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        listed = (
            await client.get("/api/agents/fake@canonical/runs", params={"source": "app"})
        ).json()
        assert [r["run_id"] for r in listed["runs"]] == ["msg-3", "msg-2"]
        assert listed["total"] == 2


async def test_a_harness_capping_its_page_size_is_paged_at_that_size(
    tmp_path, stub_judges, session_factory
):
    harness = FakeHarness(page_cap=2)
    for i in range(5):
        harness.runs[f"app-{i}"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        listed = (
            await client.get("/api/agents/fake@canonical/runs", params={"source": "app"})
        ).json()
        assert len(listed["runs"]) == 5 and listed["total"] == 5


async def test_run_id_filter_returns_stored_runs_only(api):
    client, _ = api
    exp = await create(client)
    await record(client, exp)
    params = [("run_id", "sess-9"), ("run_id", "unknown")]
    listed = (await client.get("/api/agents/fake@admin/runs", params=params)).json()
    assert [r["run_id"] for r in listed["runs"]] == ["sess-9"]


async def test_harness_scan_stops_at_the_cap_with_a_note(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    monkeypatch.setattr(pull_core, "HARNESS_PAGE", 1)
    monkeypatch.setattr(pull_core, "MAX_HARNESS_SCAN", 1)
    harness = FakeHarness()
    async with api_env(tmp_path, session_factory, harness) as (client, _):
        body = {"dataset": "finance@canonical", "agent": "fake@canonical"}
        exp = (await client.post("/api/experiments", json=body)).json()
        assert (await wait_for(client, exp["id"]))["status"] == "completed"
        listed = (
            await client.get("/api/agents/fake@canonical/runs", params={"source": "app"})
        ).json()
        assert listed["runs"] == [] and listed["harness_note"] == pull_core.SCAN_NOTE


@pytest.mark.parametrize(
    "url",
    [
        "/api/agents/fake@canonical/runs/r/files/..%2F..%2F..%2Finternal",
        "/api/agents/fake@canonical/runs/r/files/a/%2E%2E/%2E%2E/internal",
        "/api/agents/fake@canonical/runs/r/files/%2F..%2F..%2Finternal",
        "/api/agents/fake@canonical/runs/%2E%2E/files/x",
        "/api/agents/fake@canonical/runs/%2E%2E/files",
        "/api/agents/fake@canonical/runs/r/zip?path=../internal",
        "/api/agents/fake@canonical/runs/%2E%2E/trajectory",
    ],
)
async def test_traversal_paths_are_refused(shared, url):
    client, _, _ = shared
    resp = await client.get(url)
    assert resp.status_code == 400, resp.text


def test_artifact_keys_refuse_traversal():
    with pytest.raises(ValueError):
        artifact_key(1, "r", "../../trajectories/2/x.json")
    with pytest.raises(ValueError):
        artifact_key(1, "..", "x")
