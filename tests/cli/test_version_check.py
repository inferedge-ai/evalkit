import httpx
import pytest
from typer.testing import CliRunner

from evalkit import version
from evalkit.cli.client import ApiClient, ApiError
from evalkit.cli.main import app as cli_app

runner = CliRunner()


def client_with_server_version(remote: str | None) -> ApiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"X-Evalkit-Version": remote} if remote else {}
        return httpx.Response(200, json={"ok": True}, headers=headers)

    return ApiClient("http://t", transport=httpx.MockTransport(handler))


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(version, "current", lambda: "0.1.0+gaaa0000")


def test_same_build_is_silent(local, capsys):
    client_with_server_version("0.1.0+gaaa0000").get("/api/health")
    assert capsys.readouterr().err == ""


def test_different_build_nudges_once(local, capsys):
    client = client_with_server_version("0.1.0+gbbb0000")
    client.get("/api/health")
    client.get("/api/health")
    err = capsys.readouterr().err
    assert err.count("run `evalkit update --server http://t`") == 1
    assert "0.1.0+gaaa0000" in err
    assert "0.1.0+gbbb0000" in err


def test_incompatible_raises(local):
    with pytest.raises(ApiError):
        client_with_server_version("1.0.0+gbbb0000").get("/api/health")


def test_a_refused_write_never_reaches_the_server(local):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={}, headers={"X-Evalkit-Version": "0.2.0"})

    client = ApiClient("http://t", transport=httpx.MockTransport(handler))
    with pytest.raises(ApiError):
        client.post("/api/tokens", json={"agent": "x"})
    assert seen == ["GET /api/health"]


def test_the_version_is_checked_once_before_writes(local):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(200, json={}, headers={"X-Evalkit-Version": "0.1.0+gaaa0000"})

    client = ApiClient("http://t", transport=httpx.MockTransport(handler))
    client.post("/api/a")
    client.post("/api/b")
    assert seen == ["GET /api/health", "POST /api/a", "POST /api/b"]


def test_missing_header_is_silent(local, capsys):
    client_with_server_version(None).get("/api/health")
    assert capsys.readouterr().err == ""


def test_version_flag(local):
    result = runner.invoke(cli_app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == "0.1.0+gaaa0000"
