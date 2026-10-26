import hashlib
import re
from pathlib import Path

import httpx
import pytest

from evalkit import version
from evalkit.api.app import create_app
from evalkit.api.cli_dist import load_wheel
from evalkit.core.runner import RunnerDeps
from evalkit.db.store import PgStore
from tests.fake_agents import FakeAgents, FakeEvaluators, fake_credentials
from tests.fake_blob import DictBlobStore
from tests.native import make_cfg

RUNNING = "0.1.0+gabc1234"


def make_wheel(directory: Path, ver: str = RUNNING) -> Path:
    path = directory / f"evalkit-{ver}-py3-none-any.whl"
    path.write_bytes(b"PK\x03\x04fake wheel bytes")
    return path


def make_app(tmp_path, wheel_dir):
    store = PgStore(object())
    cfg = make_cfg(tmp_path)
    deps = RunnerDeps(
        cfg=cfg,
        store=store,
        blob_store=DictBlobStore(),
        agents=FakeAgents(),
        evaluators=FakeEvaluators(),
        credentials=fake_credentials(),
    )
    return create_app(
        cfg,
        runner_deps=deps,
        session_factory=object(),
        check_schema=False,
        wheel_dir=wheel_dir,
    )


@pytest.fixture
def running(monkeypatch):
    monkeypatch.setattr(version, "current", lambda: RUNNING)


async def test_every_response_carries_version_header(tmp_path, running):
    app = make_app(tmp_path, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        ok = await c.get("/api/health")
        missing = await c.get("/api/nope")
    assert ok.headers["X-Evalkit-Version"] == RUNNING
    assert missing.status_code == 404
    assert missing.headers["X-Evalkit-Version"] == RUNNING


async def test_no_wheel_dir_returns_404(tmp_path, running):
    app = make_app(tmp_path, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        info = await c.get("/api/cli")
        wheel = await c.get("/api/cli/wheel")
    assert info.status_code == 404
    assert info.json() == {"detail": "wheel not found"}
    assert wheel.status_code == 404
    assert wheel.json() == {"detail": "wheel not found"}


async def test_serves_wheel_with_checksum(tmp_path, running):
    wheel_dir = tmp_path / "dist"
    wheel_dir.mkdir()
    path = make_wheel(wheel_dir)
    app = make_app(tmp_path, wheel_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        info = await c.get("/api/cli")
        wheel = await c.get("/api/cli/wheel")
    assert info.status_code == 200
    assert info.json() == {
        "version": RUNNING,
        "filename": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "size": path.stat().st_size,
    }
    assert wheel.status_code == 200
    assert wheel.content == path.read_bytes()
    assert wheel.headers["content-disposition"] == f'attachment; filename="{path.name}"'


def test_load_wheel_rejects_version_mismatch(tmp_path):
    make_wheel(tmp_path, "0.1.0+gother00")
    with pytest.raises(RuntimeError, match="0.1.0\\+gother00"):
        load_wheel(tmp_path, RUNNING)


def test_load_wheel_rejects_two_wheels(tmp_path):
    make_wheel(tmp_path, RUNNING)
    make_wheel(tmp_path, "0.1.0+gzzz0000")
    with pytest.raises(RuntimeError, match="2 wheels"):
        load_wheel(tmp_path, RUNNING)


def test_load_wheel_empty_dir_is_none(tmp_path):
    assert load_wheel(tmp_path, RUNNING) is None
    assert load_wheel(None, RUNNING) is None


async def test_simple_index_points_at_the_served_wheel(tmp_path, running):
    wheel_dir = tmp_path / "dist"
    wheel_dir.mkdir()
    path = make_wheel(wheel_dir)
    app = make_app(tmp_path, wheel_dir)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        root = await c.get("/api/cli/simple/")
        project = await c.get("/api/cli/simple/evalkit/")
        href = re.search(r'href="([^"]+)"', project.text)
        assert href is not None
        url = httpx.URL("http://t/api/cli/simple/evalkit/").join(href.group(1))
        wheel = await c.get(url.copy_with(fragment=None))
        unknown = await c.get("/api/cli/simple/httpx/")
        other = await c.get("/api/cli/files/evalkit-9.9.9-py3-none-any.whl")
    assert root.status_code == 200
    assert root.headers["content-type"].startswith("text/html")
    assert 'href="evalkit/"' in root.text
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert url.fragment == f"sha256={digest}"
    assert url.path == f"/api/cli/files/{path.name}"
    assert wheel.status_code == 200
    assert wheel.content == path.read_bytes()
    assert unknown.status_code == 404
    assert other.status_code == 404


async def test_simple_index_is_404_without_a_wheel(tmp_path, running):
    app = make_app(tmp_path, None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        root = await c.get("/api/cli/simple/")
        project = await c.get("/api/cli/simple/evalkit/")
    assert root.status_code == 404
    assert project.status_code == 404
