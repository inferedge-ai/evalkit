import asyncio
import hashlib
import json

import httpx
import pytest
from sqlalchemy import func, select

from evalkit.contracts.enums import ExperimentStatus
from evalkit.db.models import FixtureBlob
from tests.fake_harness import FakeHarness
from tests.native import (
    FILE_BYTES,
    login_as,
    put_fixture,
    seed_env,
    serve,
    suite_case,
    sync_cases,
    wait_for,
)

FINANCE = "finance@canonical"
AGENT = "fake@canonical"


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    fake_harness = FakeHarness(workspace_files={"outputs/report.md": b"# report"})
    env = await seed_env(session_factory, tmp_path, fake_harness)
    async with serve(env, tmp_path) as client:
        yield client, env, fake_harness


async def run_shared(client: httpx.AsyncClient, **extra) -> dict:
    resp = await client.post("/api/experiments", json={"dataset": FINANCE, "agent": AGENT, **extra})
    assert resp.status_code == 200, resp.text
    return await wait_for(client, resp.json()["id"])


async def test_auth_wall(api):
    client, *_ = api
    bare = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    resp = await bare.get("/api/datasets")
    assert resp.status_code == 401
    assert (await bare.get("/api/health")).status_code == 200
    await bare.aclose()


async def test_security_headers(api):
    client, *_ = api
    for base in ("http://test", "https://test"):
        bare = httpx.AsyncClient(transport=client._transport, base_url=base)
        for path in ("/api/health", "/api/datasets"):
            headers = (await bare.get(path)).headers
            assert headers["Content-Security-Policy"].startswith("default-src 'self'")
            assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
            assert "img-src 'self' data: blob:" in headers["Content-Security-Policy"]
            assert "frame-src 'self' blob:" in headers["Content-Security-Policy"]
            assert headers["X-Frame-Options"] == "DENY"
            assert headers["X-Content-Type-Options"] == "nosniff"
            assert headers["Referrer-Policy"] == "no-referrer"
            assert ("Strict-Transport-Security" in headers) == (base == "https://test")
        await bare.aclose()


async def test_swagger_and_redoc_are_off(api):
    client, *_ = api
    for path in ("/docs", "/redoc"):
        body = (await client.get(path)).text.lower()
        assert "swagger" not in body
        assert "redoc" not in body


async def test_datasets_and_cases(api):
    client, *_ = api
    datasets = (await client.get("/api/datasets")).json()
    assert datasets[0]["ref"] == FINANCE
    cases = (await client.get(f"/api/datasets/{FINANCE}/cases")).json()
    assert {c["case_id"] for c in cases} == {"case-1", "case-2"}
    filtered = (await client.get(f"/api/datasets/{FINANCE}/cases", params={"q": "q1"})).json()
    assert [c["case_id"] for c in filtered] == ["case-1"]
    splits = (await client.get(f"/api/datasets/{FINANCE}/splits")).json()
    assert splits == [{"name": "finance", "count": 2}]


async def test_meta_reports_accent_color(api):
    client, *_ = api
    meta = (await client.get("/api/meta")).json()
    assert meta["accent_color"] is None
    assert "timezone" not in meta
    assert meta["agents"] == [AGENT]
    await client.patch(f"/api/agents/{AGENT}", json={"repo_url": "https://git/shared"})
    await client.post(
        "/api/agents", json={"workspace": "admin", "name": "fake2", "repo_url": "https://git/me"}
    )
    repos = (await client.get("/api/meta")).json()["agent_repos"]
    assert repos == {AGENT: "https://git/shared", "fake2@admin": "https://git/me"}


async def test_fixture_download(api):
    client, env, _ = api
    file = await put_fixture(env, b"line,value\n", key="balance-sheet.csv")
    resp = await client.get(
        f"/api/fixtures/sha256/{file.sha256}", params={"name": "balance-sheet.csv"}
    )
    assert resp.status_code == 200
    assert resp.content == b"line,value\n"
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == (
        "attachment; filename=\"balance-sheet.csv\"; filename*=UTF-8''balance-sheet.csv"
    )
    assert (await client.get(f"/api/fixtures/sha256/{'0' * 64}")).status_code == 404
    assert (await client.get("/api/fixtures/sha256/nope")).status_code == 400


async def test_fixture_list_by_workspace(api):
    client, env, _ = api
    bs = await put_fixture(env, b"bs", key="bs.csv")
    pl = await put_fixture(env, b"pl", key="pl.csv")
    mine = await put_fixture(env, b"mine", key="mine.csv")
    await sync_cases(
        env,
        [
            suite_case("fx-1", "q", fixtures=["bs.csv"]),
            suite_case("fx-2", "q", fixtures=["pl.csv"]),
        ],
        dataset="docs",
        files={"bs.csv": bs, "pl.csv": pl},
    )
    await sync_cases(
        env,
        [suite_case("fx-3", "q", fixtures=["mine.csv"])],
        dataset="docs",
        workspace="admin",
        files={"mine.csv": mine},
    )
    canonical = (await client.get("/api/fixtures", params={"workspace": "canonical"})).json()
    assert [i["key"] for i in canonical["items"]] == ["bs.csv", "pl.csv"]
    assert canonical["total"] == 2 and canonical["items"][0]["datasets"] == ["docs"]
    assert canonical["items"][0]["uploaded_by"] == "admin"
    own = (await client.get("/api/fixtures")).json()
    assert [i["key"] for i in own["items"]] == ["mine.csv"]
    searched = (
        await client.get("/api/fixtures", params={"workspace": "canonical", "q": "BS"})
    ).json()
    assert [i["key"] for i in searched["items"]] == ["bs.csv"]
    paged = (
        await client.get(
            "/api/fixtures", params={"workspace": "canonical", "limit": 1, "offset": 1}
        )
    ).json()
    assert [i["key"] for i in paged["items"]] == ["pl.csv"] and paged["total"] == 2
    assert (await client.get("/api/fixtures", params={"workspace": "nope"})).status_code == 404


async def test_experiment_lifecycle_and_scorecard(api):
    client, env, _ = api
    status = await run_shared(client, repetitions=1)
    assert status["status"] == "completed"
    assert status["progress"] == {"total": 2, "ran": 2, "evaluated": 2}
    assert status["case_states"] == {"case-1": "evaluated", "case-2": "evaluated"}
    exp_id = status["id"]

    scorecard = (await client.get(f"/api/experiments/{exp_id}/scorecard")).json()
    assert scorecard["buckets"]["content"] == 0.5
    assert any(e["name"] == "answer_rubric_score" for e in scorecard["evaluators"])

    listed = (await client.get(f"/api/datasets/{FINANCE}/experiments")).json()
    assert listed and listed[0]["id"] == exp_id
    assert listed[0]["scores"]["content"] == 0.5
    assert listed[0]["progress"]["evaluated"] == 2
    everywhere = (await client.get("/api/experiments", params={"workspace": "canonical"})).json()
    assert [e["id"] for e in everywhere] == [exp_id]

    run_id = (await env.details(exp_id))[0].experiment_run_id
    trajectory = (await client.get(f"/api/experiments/{exp_id}/runs/{run_id}/trajectory")).json()
    assert trajectory["schema_version"] == "ATIF-v1.8"


async def test_second_resume_while_live_conflicts(api):
    client, env, _ = api
    exp_id = (await run_shared(client))["id"]
    await env.store.transition(exp_id, ExperimentStatus.EVALUATING, "cli:other@host:1")
    resumed = await client.post(f"/api/experiments/{exp_id}/resume")
    assert resumed.status_code == 409
    assert "held by cli:other@host:1" in resumed.json()["detail"]
    assert (await client.post(f"/api/experiments/{exp_id}/evaluate")).status_code == 409


async def test_ui_create_without_cases_records_the_last_git_sync(api):
    client, *_ = api
    resp = await client.post(
        "/api/sync",
        params={"dataset": "finance", "workspace": "canonical", "sha": "abc123", "branch": "dev"},
        files=[("files", ("c.yaml", CASES))],
    )
    assert resp.status_code == 200, resp.text
    created = await client.post(
        "/api/experiments", json={"dataset": FINANCE, "agent": AGENT, "include_drafts": True}
    )
    assert created.status_code == 200, created.text
    assert created.json()["config"]["revision"] == {"sha": "abc123", "branch": "dev", "dirty": None}
    await wait_for(client, created.json()["id"])


async def test_lint_endpoint(api):
    client, *_ = api
    bad_yaml = b"- id: a\n  tags: ['working:x']\n  input: {question: q}\n"
    resp = await client.post("/api/lint", files=[("files", ("bad.yaml", bad_yaml))])
    body = resp.json()
    assert body["errors"] >= 1
    assert any(f["code"] == "E-RESERVED-TAG" for f in body["findings"])


def _registry_yaml(key: str, data: bytes, context: str = "ctx") -> bytes:
    return (
        f"fixtures:\n- key: {key}\n  sha256: {hashlib.sha256(data).hexdigest()}\n"
        f"  context: {context}\n"
    ).encode()


async def test_sync_requires_dataset(api):
    client, *_ = api
    resp = await client.post(
        "/api/sync",
        files=[("files", ("c.yaml", b"- id: x\n  input: {question: 'q 2024'}\n"))],
    )
    assert resp.status_code == 422


async def test_sync_into_existing_suite_dataset(api):
    client, *_ = api
    resp = await client.post(
        "/api/sync",
        params={"dataset": "finance", "workspace": "canonical", "sha": "sha-1"},
        files=[("files", ("c.yaml", b"- id: x\n  input: {question: 'q as of 2024-01-01'}\n"))],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1
    dataset = (await client.get(f"/api/datasets/{FINANCE}")).json()
    assert dataset["last_sync"]["detail"]["sha"] == "sha-1"
    assert dataset["last_sync"]["actor"] == "admin"


async def test_sync_resolves_uploaded_fixtures_and_ignores_non_yaml(api):
    client, env, _ = api
    data = b"%PDF"
    digest = hashlib.sha256(data).hexdigest()
    assert (await client.put(f"/api/fixtures/sha256/{digest}", content=data)).status_code == 200
    case_yaml = b"- id: x\n  input: {question: 'q as of 2024-01-01'}\n  fixtures: [doc.pdf]\n"
    resp = await client.post(
        "/api/sync",
        params={"dataset": "custom", "workspace": "canonical", "prune": "true"},
        files=[
            ("files", ("fixtures.yaml", _registry_yaml("doc.pdf", data))),
            ("files", ("fixtures/other.pdf", b"never written")),
            ("files", ("c.yaml", case_yaml)),
        ],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["created"] == 1
    [case] = (await client.get("/api/datasets/custom@canonical/cases")).json()
    assert case["files"] == [{"key": "doc.pdf", "sha256": digest, "context": "ctx"}]
    assert "files" not in case["input"]
    assert not any(key.endswith("other.pdf") for key in env.blob.objects)


async def test_sync_missing_fixture_aborts(api):
    client, *_ = api
    case_yaml = b"- id: x\n  input: {question: 'q as of 2024-01-01'}\n  fixtures: [gone.pdf]\n"
    resp = await client.post(
        "/api/sync",
        params={"dataset": "custom", "workspace": "canonical"},
        files=[
            ("files", ("fixtures.yaml", _registry_yaml("gone.pdf", b"x"))),
            ("files", ("c.yaml", case_yaml)),
        ],
    )
    body = resp.json()
    assert body["aborted"]
    assert [f["code"] for f in body["findings"] if f["severity"] == "error"] == [
        "E-FIXTURE-MISSING"
    ]


def overlay_yaml(target: str) -> bytes:
    return (
        f"- extends: {target}\n  agents: [fake]\n  evaluators:\n    custom:\n"
        "      required_tool_calls: {tools: [retrieve_passages]}\n"
    ).encode()


async def test_lint_endpoint_with_dataset_checks_overlay_targets(api):
    client, *_ = api
    shared = b"- id: case-1\n  input: {question: 'q as of 2024-01-01'}\n"
    params = {"dataset": "mine@canonical"}
    resp = await client.post("/api/lint", params=params, files=[("files", ("c.yaml", shared))])
    assert not any(f["code"] == "E-ID-COLLISION" for f in resp.json()["findings"])
    for target, expected in (("case-1", False), ("nope", True)):
        body = overlay_yaml(target)
        resp = await client.post("/api/lint", params=params, files=[("files", ("o.yaml", body))])
        codes = [f["code"] for f in resp.json()["findings"]]
        assert ("E-OVERLAY-TARGET" in codes) is expected, codes


async def test_sync_with_no_files_prunes_existing_dataset(api):
    client, *_ = api
    assert (await client.get(f"/api/datasets/{FINANCE}/cases")).json()
    resp = await client.post(
        "/api/sync", params={"dataset": "finance", "workspace": "canonical", "prune": "true"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["deleted"] >= 1
    assert (await client.get(f"/api/datasets/{FINANCE}/cases")).json() == []


async def test_removed_routes_are_gone(api):
    client, *_ = api
    spec = (await client.get("/openapi.json")).json()
    assert "/api/datasets/install" not in spec["paths"]
    assert "/api/fixtures/bundle" not in spec["paths"]
    assert "/api/datasets/{slug}/baseline" not in spec["paths"]
    assert not any("/examples" in path for path in spec["paths"])


async def test_harness_check(api):
    client, _, fake_harness = api
    resp = await client.post(f"/api/agents/{AGENT}/check")
    checks = resp.json()["checks"]
    assert checks["capabilities"]["status"] == "pass"
    assert checks["run"]["status"] == "pass"
    assert checks["files"]["status"] == "pass"
    assert [name for name, _ in fake_harness.received_files["sess-1"]] == ["evalkit-check.txt"]
    assert checks["trajectory"]["status"] == "pass"
    assert checks["workspace"]["status"] == "pass"
    assert checks["run_listing"]["status"] == "pass"
    assert checks["stop"]["status"] == "pass"


async def test_users_admin(api):
    client, *_ = api
    resp = await client.post("/api/users", json={"username": "bob", "password": "password"})
    assert resp.status_code == 200
    users = (await client.get("/api/users")).json()
    assert {u["username"] for u in users} == {"admin", "alice", "akshat", "bob"}
    assert (await client.post("/api/users/bob/archive")).status_code == 200
    users = {u["username"]: u for u in (await client.get("/api/users")).json()}
    assert users["bob"]["archived"] and users["admin"]["is_admin"]
    assert (await client.post("/api/users/admin/archive")).status_code == 409
    assert (await client.post("/api/users/admin/demote")).status_code == 409
    assert (await client.post("/api/users/ghost/archive")).status_code == 404
    resp = await client.post("/api/users", json={"username": "canonical", "password": "password"})
    assert resp.status_code == 409


@pytest.mark.parametrize(
    "password, detail",
    [("seven77", "at least 8 characters"), ("é" * 37, "longer than 72 bytes")],
)
async def test_users_password_rules_are_400(api, password, detail):
    client, *_ = api
    resp = await client.post("/api/users", json={"username": "bob", "password": password})
    assert resp.status_code == 400 and detail in resp.json()["detail"]
    resp = await client.post("/api/users/alice/password", json={"password": password})
    assert resp.status_code == 400 and detail in resp.json()["detail"]


async def test_run_with_invalid_inputs_returns_400(api):
    client, _, fake_harness = api
    fake_harness.capabilities["input_schema"] = {"type": "object", "required": ["source_type"]}
    resp = await client.post("/api/experiments", json={"dataset": FINANCE, "agent": AGENT})
    assert resp.status_code == 400
    assert "source_type" in resp.json()["detail"]


async def test_lint_endpoint_with_agent_checks_input_schema(api):
    client, _, fake_harness = api
    fake_harness.capabilities["input_schema"] = {"type": "object", "required": ["source_type"]}
    case_yaml = b"- id: new-1\n  input: {question: 'q as of 2024-01-01'}\n"
    resp = await client.post(
        "/api/lint", params={"agent": AGENT}, files=[("files", ("c.yaml", case_yaml))]
    )
    assert resp.status_code == 200
    assert [f["case_id"] for f in resp.json()["findings"] if f["code"] == "E-INPUT-SCHEMA"] == [
        "new-1"
    ]


async def test_lint_endpoint_never_calls_a_personal_harness(api):
    client, *_ = api
    await client.post(
        "/api/agents", json={"workspace": "admin", "name": "mine", "harness_url": "http://h"}
    )
    case_yaml = b"- id: new-1\n  input: {question: 'q as of 2024-01-01'}\n"
    files = [("files", ("c.yaml", case_yaml))]
    refused = await client.post("/api/lint", params={"agent": "mine@admin"}, files=files)
    assert refused.status_code == 400 and "does not drive" in refused.json()["detail"]
    schema = json.dumps({"type": "object", "required": ["source_type"]})
    sent = await client.post("/api/lint", data={"input_schema": schema}, files=files)
    assert sent.status_code == 200
    assert [f["code"] for f in sent.json()["findings"]].count("E-INPUT-SCHEMA") == 1
    for bad in ("{not json", "[1, 2]", '"text"'):
        rejected = await client.post("/api/lint", data={"input_schema": bad}, files=files)
        assert rejected.status_code == 400 and "input_schema" in rejected.json()["detail"]


async def test_lint_endpoint_unknown_agent_404(api):
    client, *_ = api
    case_yaml = b"- id: new-1\n  input: {question: 'q as of 2024-01-01'}\n"
    resp = await client.post(
        "/api/lint", params={"agent": "nope@canonical"}, files=[("files", ("c.yaml", case_yaml))]
    )
    assert resp.status_code == 404


async def test_shutdown_marks_in_flight_experiment_interrupted(
    tmp_path, stub_judges, session_factory
):
    fake_harness = FakeHarness(complete_after_polls=10_000_000)
    env = await seed_env(session_factory, tmp_path, fake_harness)
    async with serve(env, tmp_path) as client:
        resp = await client.post("/api/experiments", json={"dataset": FINANCE, "agent": AGENT})
        exp_id = resp.json()["id"]
        while not fake_harness.runs:
            await asyncio.sleep(0.01)
        assert (await env.experiment(exp_id)).status is ExperimentStatus.RUNNING
    exp = await env.experiment(exp_id)
    assert exp.status is ExperimentStatus.FAILED
    assert exp.status_error == "interrupted: evalkit server shut down"
    assert exp.lease_holder is None


CASES = b"- id: ws-1\n  input: {question: 'q 2024'}\n"


async def test_sync_defaults_to_caller_workspace(api):
    client, *_ = api
    alice = await login_as(client, "alice")
    resp = await alice.post(
        "/api/sync", params={"dataset": "scratch"}, files=[("files", ("c.yaml", CASES))]
    )
    assert resp.status_code == 200, resp.text
    dataset = (await client.get("/api/datasets/scratch@alice")).json()
    assert dataset["workspace"] == "alice"
    assert dataset["last_sync"]["actor"] == "alice"
    assert (await client.get("/api/datasets/scratch@canonical")).status_code == 404


async def test_sync_canonical_is_admin_only(api):
    client, *_ = api
    alice = await login_as(client, "alice")
    resp = await alice.post(
        "/api/sync",
        params={"dataset": "scratch", "workspace": "canonical"},
        files=[("files", ("c.yaml", CASES))],
    )
    assert resp.status_code == 403
    resp = await client.post(
        "/api/sync",
        params={
            "dataset": "scratch",
            "workspace": "canonical",
            "branch": "develop",
            "dirty": "false",
        },
        files=[("files", ("c.yaml", CASES))],
    )
    assert resp.status_code == 200, resp.text
    dataset = (await client.get("/api/datasets/scratch@canonical")).json()
    assert dataset["workspace"] == "canonical"
    detail = dataset["last_sync"]["detail"]
    assert (detail["branch"], detail["dirty"], detail["counts"]["created"]) == ("develop", False, 1)


async def test_sync_into_another_users_workspace_is_forbidden(api):
    client, *_ = api
    resp = await client.post(
        "/api/sync",
        params={"dataset": "scratch", "workspace": "alice"},
        files=[("files", ("c.yaml", CASES))],
    )
    assert resp.status_code == 403


async def test_workspace_dataset_has_no_review(api):
    client, *_ = api
    alice = await login_as(client, "alice")
    await alice.post(
        "/api/sync", params={"dataset": "scratch"}, files=[("files", ("c.yaml", CASES))]
    )
    rows = (await client.get("/api/datasets/scratch@alice/cases")).json()
    assert rows[0]["review"] is None and rows[0]["review_state"] is None
    resp = await client.post(
        "/api/datasets/scratch@alice/review-requests",
        json={"selection": {"all": True}, "reviewer": "admin"},
    )
    assert resp.status_code == 409
    queue = (await client.get("/api/review/queue", params={"state": "review_requested"})).json()
    assert all(item["dataset"] != "scratch@alice" for item in queue["items"])


async def _blob_rows(env) -> int:
    async with env.session_factory() as s:
        found = await s.scalar(select(func.count()).select_from(FixtureBlob))
    assert found is not None
    return found


async def test_fixture_upload_is_content_addressed(api):
    client, env, _ = api
    digest = hashlib.sha256(FILE_BYTES).hexdigest()
    alice = await login_as(client, "alice")
    resp = await alice.put(f"/api/fixtures/sha256/{digest}", content=FILE_BYTES)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"sha256": digest, "size": len(FILE_BYTES)}
    assert env.blob.objects[f"fixtures/sha256/{digest}"] == FILE_BYTES
    again = await client.put(f"/api/fixtures/sha256/{digest}", content=FILE_BYTES)
    assert again.status_code == 200
    assert await _blob_rows(env) == 1
    async with env.session_factory() as s:
        row = await s.get(FixtureBlob, digest)
    assert row is not None and row.uploaded_by == env.user_ids["alice"]
    assert row.size == len(FILE_BYTES)

    wrong = await client.put(f"/api/fixtures/sha256/{'0' * 64}", content=b"other")
    assert wrong.status_code == 400
    assert wrong.json()["detail"] == (
        f"bytes hash to {hashlib.sha256(b'other').hexdigest()}, not {'0' * 64}"
    )
    assert (await client.put("/api/fixtures/sha256/ABC", content=b"x")).status_code == 400
    assert await _blob_rows(env) == 1

    assert (await client.head(f"/api/fixtures/sha256/{digest}")).status_code == 200
    assert (await client.head(f"/api/fixtures/sha256/{'1' * 64}")).status_code == 404
    got = await client.get(f"/api/fixtures/sha256/{digest}")
    assert got.status_code == 200 and got.content == FILE_BYTES


async def test_unknown_dataset_returns_404_with_its_name(api):
    client, *_ = api
    resp = await client.post("/api/experiments", json={"dataset": "finance@admin", "agent": AGENT})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown dataset: finance@admin"
    for path in ("cases", "splits", "experiments"):
        resp = await client.get(f"/api/datasets/nope@canonical/{path}")
        assert (resp.status_code, resp.json()["detail"]) == (
            404,
            "unknown dataset: nope@canonical",
        )
