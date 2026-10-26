import hashlib
from pathlib import Path

import httpx
import pytest

from evalkit import version
from evalkit.cli.client import ApiClient, ApiError
from evalkit.cli.update import install_command, run_update

REMOTE = "0.1.0+gbbb0000"
WHEEL_NAME = f"evalkit-{REMOTE}-py3-none-any.whl"
WHEEL_BYTES = b"PK\x03\x04wheel"


def fake_server(sha256: str | None = None, info_status: int = 200) -> httpx.MockTransport:
    digest = sha256 or hashlib.sha256(WHEEL_BYTES).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"X-Evalkit-Version": REMOTE}
        if request.url.path == "/api/cli":
            if info_status != 200:
                return httpx.Response(404, json={"detail": "wheel not found"}, headers=headers)
            body = {
                "version": REMOTE,
                "filename": WHEEL_NAME,
                "sha256": digest,
                "size": len(WHEEL_BYTES),
            }
            return httpx.Response(200, json=body, headers=headers)
        if request.url.path == "/api/cli/wheel":
            return httpx.Response(200, content=WHEEL_BYTES, headers=headers)
        return httpx.Response(404, headers=headers)

    return httpx.MockTransport(handler)


@pytest.fixture
def stale(monkeypatch):
    monkeypatch.setattr(version, "current", lambda: "0.1.0+gaaa0000")


def test_install_command_detects_uv_tool(tmp_path):
    prefix = Path.home() / ".local" / "share" / "uv" / "tools" / "evalkit"
    assert install_command(prefix, tmp_path / "w.whl") == [
        "uv",
        "tool",
        "install",
        "--reinstall",
        str(tmp_path / "w.whl"),
    ]


def test_install_command_detects_pipx(tmp_path):
    prefix = Path.home() / ".local" / "pipx" / "venvs" / "evalkit"
    assert install_command(prefix, tmp_path / "w.whl") == [
        "pipx",
        "install",
        "--force",
        str(tmp_path / "w.whl"),
    ]


def test_install_command_unknown_prefix(tmp_path):
    assert install_command(tmp_path / ".venv", tmp_path / "w.whl") is None


def test_already_current(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    calls: list[list[str]] = []
    client = ApiClient("http://t", transport=fake_server())
    run_update(client, prefix=tmp_path, run=calls.append, cwd=tmp_path)
    assert calls == []
    assert "already current" in capsys.readouterr().out


def test_runs_detected_installer(stale, tmp_path):
    calls: list[list[str]] = []
    prefix = Path("/home/x/.local/share/uv/tools/evalkit")
    client = ApiClient("http://t", transport=fake_server())
    run_update(client, prefix=prefix, run=calls.append, cwd=tmp_path)
    assert len(calls) == 1
    assert calls[0][:4] == ["uv", "tool", "install", "--reinstall"]
    assert calls[0][4].endswith(WHEEL_NAME)


def test_update_works_across_an_incompatible_minor(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "current", lambda: "0.0.9+gaaa0000")
    calls: list[list[str]] = []
    prefix = Path("/home/x/.local/share/uv/tools/evalkit")
    checked = ApiClient("http://t", transport=fake_server())
    with pytest.raises(ApiError):
        run_update(checked, prefix=prefix, run=calls.append, cwd=tmp_path)
    client = ApiClient("http://t", transport=fake_server(), check_version=False)
    run_update(client, prefix=prefix, run=calls.append, cwd=tmp_path)
    assert len(calls) == 1


def test_checksum_mismatch_raises(stale, tmp_path):
    client = ApiClient("http://t", transport=fake_server(sha256="0" * 64))
    with pytest.raises(ApiError):
        run_update(client, prefix=tmp_path, run=lambda c: None, cwd=tmp_path)


def test_unknown_prefix_prints_commands_and_fails(stale, tmp_path, capsys):
    client = ApiClient("http://t", transport=fake_server())
    with pytest.raises(ApiError):
        run_update(client, prefix=tmp_path / ".venv", run=lambda c: None, cwd=tmp_path)
    out = capsys.readouterr().out
    assert "uv tool install --reinstall" in out
    assert "pipx install --force" in out
    assert (tmp_path / WHEEL_NAME).read_bytes() == WHEEL_BYTES


def test_no_wheel_on_server(stale, tmp_path):
    client = ApiClient("http://t", transport=fake_server(info_status=404))
    with pytest.raises(ApiError):
        run_update(client, prefix=tmp_path, run=lambda c: None, cwd=tmp_path)


def test_update_replaces_a_vendored_wheel_and_syncs(tmp_path, stale):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    old = vendor / "evalkit-0.1.0+gaaa0000-py3-none-any.whl"
    old.write_bytes(b"old")
    (tmp_path / "pyproject.toml").write_text(
        f'dependencies = ["evalkit[otel] @ file:///x/vendor/{old.name}"]\n'
    )
    ran: list[list[str]] = []
    client = ApiClient("http://srv", transport=fake_server())
    run_update(client, prefix=tmp_path / "proj" / ".venv", run=ran.append, cwd=tmp_path)
    assert not old.exists() and (vendor / WHEEL_NAME).read_bytes() == WHEEL_BYTES
    assert WHEEL_NAME in (tmp_path / "pyproject.toml").read_text()
    assert ran == [["uv", "sync"]]


def test_vendor_downloads_the_wheel_the_first_time(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    ran: list[list[str]] = []
    client = ApiClient("http://srv", transport=fake_server())
    run_update(client, vendor=tmp_path / "vendor", prefix=tmp_path, run=ran.append, cwd=tmp_path)
    assert (tmp_path / "vendor" / WHEEL_NAME).read_bytes() == WHEEL_BYTES
    assert ran == []
    out = capsys.readouterr().out
    assert f'uv add "./vendor/{WHEEL_NAME}[otel]"' in out
    assert f'uv run --with "./vendor/{WHEEL_NAME}[otel]" python eval/run.py' in out


def test_vendor_rejects_a_bad_checksum_before_writing(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    client = ApiClient("http://srv", transport=fake_server(sha256="0" * 64))
    with pytest.raises(ApiError):
        run_update(client, vendor=tmp_path / "vendor", prefix=tmp_path, cwd=tmp_path)
    assert not (tmp_path / "vendor").exists()


def test_current_cli_still_replaces_a_stale_vendored_wheel(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    old = vendor / "evalkit-0.1.0+gaaa0000-py3-none-any.whl"
    old.write_bytes(b"old")
    ran: list[list[str]] = []
    client = ApiClient("http://srv", transport=fake_server())
    run_update(client, prefix=tmp_path, run=ran.append, cwd=tmp_path)
    assert not old.exists() and (vendor / WHEEL_NAME).read_bytes() == WHEEL_BYTES
    assert ran == []


def test_current_vendored_wheel_is_left_alone(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / WHEEL_NAME).write_bytes(WHEEL_BYTES)
    client = ApiClient("http://srv", transport=fake_server())
    run_update(client, vendor=vendor, prefix=tmp_path, run=lambda c: None, cwd=tmp_path)
    assert "already current" in capsys.readouterr().out


def test_stale_cli_and_vendored_wheel_update_together(stale, tmp_path):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    old = vendor / "evalkit-0.1.0+gaaa0000-py3-none-any.whl"
    old.write_bytes(b"old")
    ran: list[list[str]] = []
    prefix = Path("/home/x/.local/share/uv/tools/evalkit")
    client = ApiClient("http://srv", transport=fake_server())
    run_update(client, prefix=prefix, run=ran.append, cwd=tmp_path)
    assert (vendor / WHEEL_NAME).read_bytes() == WHEEL_BYTES
    assert len(ran) == 1 and ran[0][:4] == ["uv", "tool", "install", "--reinstall"]


def test_stale_cli_in_a_plain_venv_with_a_current_vendored_wheel_says_how_to_update(
    stale, tmp_path
):
    vendor = tmp_path / "vendor"
    vendor.mkdir()
    (vendor / WHEEL_NAME).write_bytes(WHEEL_BYTES)
    client = ApiClient("http://srv", transport=fake_server())
    with pytest.raises(ApiError):
        run_update(client, prefix=tmp_path / ".venv", run=lambda c: None, cwd=tmp_path)
    assert (tmp_path / WHEEL_NAME).read_bytes() == WHEEL_BYTES


def test_reinstall_refreshes_installed_skills(stale, tmp_path):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / ".evalkit-installed").write_text("evalkit-usage\n")
    calls: list[list[str]] = []
    prefix = Path("/home/x/.local/share/uv/tools/evalkit")
    client = ApiClient("http://t", transport=fake_server())
    run_update(client, prefix=prefix, run=calls.append, cwd=tmp_path)
    cli = str(prefix / "bin" / "evalkit")
    assert calls[1] == [cli, "skills", "install", "--target", str(skills.resolve())]


def test_no_skills_skips_the_refresh(stale, tmp_path):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / ".evalkit-installed").write_text("evalkit-usage\n")
    calls: list[list[str]] = []
    prefix = Path("/home/x/.local/share/uv/tools/evalkit")
    client = ApiClient("http://t", transport=fake_server())
    run_update(client, prefix=prefix, run=calls.append, cwd=tmp_path, skills=False)
    assert len(calls) == 1
