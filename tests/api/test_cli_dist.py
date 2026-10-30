import httpx
import pytest

from evalkit import version
from evalkit.api.app import create_app
from evalkit.core.runner import RunnerDeps
from evalkit.db.store import PgStore
from tests.fake_agents import FakeAgents, FakeEvaluators, fake_credentials
from tests.fake_blob import DictBlobStore
from tests.native import make_cfg

RUNNING = "0.3.0.dev57"


def make_app(tmp_path):
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
    )


@pytest.fixture
def running(monkeypatch):
    monkeypatch.setattr(version, "current", lambda: RUNNING)


async def test_every_response_carries_version_header(tmp_path, running):
    app = make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        ok = await c.get("/api/health")
        missing = await c.get("/api/nope")
    assert ok.headers["X-Evalkit-Version"] == RUNNING
    assert missing.status_code == 404
    assert missing.headers["X-Evalkit-Version"] == RUNNING


async def test_cli_info_reports_version(tmp_path, running):
    app = make_app(tmp_path)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        info = await c.get("/api/cli")
        gone = [
            await c.get(path)
            for path in ("/api/cli/wheel", "/api/cli/simple/", "/api/cli/files/x.whl")
        ]
    assert info.json() == {"version": RUNNING, "package": "evalkit-py"}
    assert [r.status_code for r in gone] == [404, 404, 404]
