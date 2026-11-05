import hashlib
from collections.abc import AsyncIterator

import pytest

from evalkit.api.limits import body_limit
from evalkit.config import Config
from tests.api.native_api import CAPS, api_env
from tests.fake_harness import FakeHarness

TOO_BIG = (
    "fixture upload is larger than the 8 bytes limit (server config limits.fixture_upload_bytes)"
)


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        yield client


def cfg(api) -> Config:
    return api.app.state.cfg


async def chunks(data: bytes) -> AsyncIterator[bytes]:
    for i in range(0, len(data), 4):
        yield data[i : i + 4]


async def test_fixture_upload_cap(api, caplog):
    caplog.set_level("WARNING", logger="evalkit.api.limits")
    cfg(api).limits.fixture_upload_bytes = 8
    big = b"0123456789"
    url = f"/api/fixtures/sha256/{hashlib.sha256(big).hexdigest()}"
    declared = await api.put(url, content=big)
    assert declared.status_code == 413 and declared.json()["detail"] == TOO_BIG
    assert "X-Evalkit-Version" in declared.headers
    streamed = await api.put(url, content=chunks(big))
    assert "content-length" not in streamed.request.headers
    assert streamed.status_code == 413 and streamed.json()["detail"] == TOO_BIG
    small = b"01234567"
    ok = await api.put(f"/api/fixtures/sha256/{hashlib.sha256(small).hexdigest()}", content=small)
    assert ok.status_code == 200, ok.text
    logged = [r.getMessage() for r in caplog.records if r.name == "evalkit.api.limits"]
    assert logged == [
        f"request body too large: route=PUT {url} limit=8 key=limits.fixture_upload_bytes "
        "received=10",
        f"request body too large: route=PUT {url} limit=8 key=limits.fixture_upload_bytes "
        "received=10",
    ]


async def test_experiment_blob_cap(api):
    body = {
        "dataset": "finance@canonical",
        "agent": "fake@admin",
        "capabilities": CAPS,
        "holder": "cli:admin@laptop:1",
    }
    created = (await api.post("/api/experiments", json=body)).json()
    url = f"/api/experiments/{created['id']}/blob/trajectories/{created['agent_id']}/s.json"
    cfg(api).limits.experiment_blob_bytes = 4
    for content in (b'{"a": 1}', chunks(b'{"a": 1}')):
        resp = await api.put(url, content=content)
        assert resp.status_code == 413
        assert resp.json()["detail"] == (
            "experiment blob is larger than the 4 bytes limit "
            "(server config limits.experiment_blob_bytes)"
        )
    assert (await api.put(url, content=b"{}")).status_code == 200


async def test_case_upload_cap(api):
    cfg(api).limits.case_upload_bytes = 64
    files = [("files", ("cases.yaml", b"x" * 200, "application/octet-stream"))]
    resp = await api.post("/api/lint", params={"dataset": "finance@canonical"}, files=files)
    assert resp.status_code == 413
    assert "limits.case_upload_bytes" in resp.json()["detail"]
    cfg(api).limits.case_upload_bytes = 10_000
    resp = await api.post("/api/lint", params={"dataset": "finance@canonical"}, files=files)
    assert resp.status_code != 413


async def test_general_request_cap(api):
    cfg(api).limits.request_bytes = 16
    body = b'{"name": "a-long-agent-name"}'
    for content in (body, chunks(body)):
        resp = await api.post(
            "/api/agents", content=content, headers={"content-type": "application/json"}
        )
        assert resp.status_code == 413
        assert resp.json()["detail"] == (
            "request body is larger than the 16 bytes limit (server config limits.request_bytes)"
        )
    assert (await api.get("/api/agents")).status_code == 200


async def test_route_caps_are_larger_than_the_general_cap():
    config = Config.model_validate(
        {
            "server": {"base_url": "http://x"},
            "db": {"url": "postgresql://x"},
            "blob": {"url": "memory://"},
        }
    )
    assert body_limit(config, "PUT", "/api/fixtures/sha256/abc").limit == 500 * 1024 * 1024
    assert body_limit(config, "PUT", "/api/experiments/3/blob/a/b.json").limit == 500 * 1024**2
    assert body_limit(config, "POST", "/api/sync").limit == 50 * 1024 * 1024
    assert body_limit(config, "POST", "/api/otel/v1/traces").key == "otel.max_batch_bytes"
    assert body_limit(config, "POST", "/api/agents").limit == 10 * 1024 * 1024
    assert body_limit(config, "GET", "/api/fixtures/sha256/abc").key == "limits.request_bytes"


async def test_fixture_upload_streams_and_leaves_no_staging_behind(api):
    blob = api.app.state.runner_deps.blob_store
    data = b"streamed fixture bytes"
    sha = hashlib.sha256(data).hexdigest()
    ok = await api.put(f"/api/fixtures/sha256/{sha}", content=chunks(data))
    assert ok.status_code == 200 and ok.json() == {"sha256": sha, "size": len(data)}
    assert await blob.get_object(f"fixtures/sha256/{sha}") == data
    again = await api.put(f"/api/fixtures/sha256/{sha}", content=b"not read")
    assert again.status_code == 200 and again.json() == {"sha256": sha, "size": len(data)}
    wrong = await api.put(f"/api/fixtures/sha256/{'0' * 64}", content=chunks(data))
    assert wrong.status_code == 400 and f"bytes hash to {sha}" in wrong.json()["detail"]
    cfg(api).limits.fixture_upload_bytes = 8
    fresh = data + b"!"
    fresh_url = f"/api/fixtures/sha256/{hashlib.sha256(fresh).hexdigest()}"
    cut = await api.put(fresh_url, content=chunks(fresh))
    assert cut.status_code == 413
    assert await blob.list_objects("fixtures/staging/") == []


async def test_fixture_and_experiment_blob_downloads_stream(api):
    data = bytes(range(256)) * (10 * 1024)
    sha = hashlib.sha256(data).hexdigest()
    assert (await api.put(f"/api/fixtures/sha256/{sha}", content=data)).status_code == 200
    got = await api.get(f"/api/fixtures/sha256/{sha}", params={"name": "bs.csv"})
    assert got.status_code == 200 and got.content == data
    assert got.headers["content-length"] == str(len(data))
    assert got.headers["content-type"].startswith("text/csv")
    assert got.headers["content-disposition"] == (
        "attachment; filename=\"bs.csv\"; filename*=UTF-8''bs.csv"
    )
    body = {
        "dataset": "finance@canonical",
        "agent": "fake@admin",
        "capabilities": CAPS,
        "holder": "cli:admin@laptop:1",
    }
    created = (await api.post("/api/experiments", json=body)).json()
    url = f"/api/experiments/{created['id']}/blob/trajectories/{created['agent_id']}/s.json"
    assert (await api.put(url, content=data)).status_code == 200
    blob = await api.get(url)
    assert blob.content == data and blob.headers["content-length"] == str(len(data))
    assert blob.headers["content-type"] == "application/octet-stream"
