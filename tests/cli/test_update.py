from pathlib import Path

import httpx
import pytest

from evalkit import version
from evalkit.cli.client import ApiClient, ApiError
from evalkit.cli.update import install_command, run_update

REMOTE = "0.3.0.dev58"
UV_PREFIX = Path("/home/x/.local/share/uv/tools/evalkit-py")
PIPX_PREFIX = Path("/home/x/.local/pipx/venvs/evalkit-py")


def fake_server() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        headers = {"X-Evalkit-Version": REMOTE}
        if request.url.path == "/api/cli":
            return httpx.Response(
                200, json={"version": REMOTE, "package": "evalkit-py"}, headers=headers
            )
        return httpx.Response(404, headers=headers)

    return httpx.MockTransport(handler)


def client(check_version: bool = False) -> ApiClient:
    return ApiClient("http://t", transport=fake_server(), check_version=check_version)


@pytest.fixture
def stale(monkeypatch):
    monkeypatch.setattr(version, "current", lambda: "0.3.0.dev57")


def test_install_command_detects_uv_tool():
    assert install_command(UV_PREFIX, "evalkit-py==1.0.0") == [
        "uv",
        "tool",
        "install",
        "--reinstall",
        "evalkit-py==1.0.0",
    ]


def test_install_command_detects_pipx():
    assert install_command(PIPX_PREFIX, "evalkit-py==1.0.0") == [
        "pipx",
        "install",
        "--force",
        "evalkit-py==1.0.0",
    ]


def test_install_command_unknown_prefix(tmp_path):
    assert install_command(tmp_path / ".venv", "evalkit-py==1.0.0") is None


def test_already_current(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    calls: list[list[str]] = []
    run_update(client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, server_extra=False)
    assert calls == []
    assert "already current" in capsys.readouterr().out


def test_uv_tool_reinstalls_the_server_version(stale, tmp_path):
    calls: list[list[str]] = []
    run_update(
        client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, skills=False, server_extra=False
    )
    assert calls == [["uv", "tool", "install", "--reinstall", f"evalkit-py=={REMOTE}"]]


def test_server_extra_is_kept(stale, tmp_path):
    calls: list[list[str]] = []
    run_update(
        client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, skills=False, server_extra=True
    )
    assert calls[0][-1] == f"evalkit-py[server]=={REMOTE}"


def test_pipx(stale, tmp_path):
    calls: list[list[str]] = []
    run_update(
        client(),
        prefix=PIPX_PREFIX,
        run=calls.append,
        cwd=tmp_path,
        skills=False,
        server_extra=False,
    )
    assert calls == [["pipx", "install", "--force", f"evalkit-py=={REMOTE}"]]


def test_unknown_installer_prints_commands_and_fails(stale, tmp_path, capsys):
    with pytest.raises(ApiError, match="could not tell how evalkit was installed"):
        run_update(
            client(),
            prefix=tmp_path / ".venv",
            run=lambda c: None,
            cwd=tmp_path,
            server_extra=False,
        )
    out = capsys.readouterr().out
    assert f'uv tool install --reinstall "evalkit-py=={REMOTE}"' in out
    assert f'pipx install --force "evalkit-py=={REMOTE}"' in out


def test_update_works_across_an_incompatible_minor(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "current", lambda: "0.2.0")
    calls: list[list[str]] = []
    with pytest.raises(ApiError):
        run_update(client(check_version=True), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path)
    run_update(
        client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, skills=False, server_extra=False
    )
    assert len(calls) == 1


def test_project_pin_is_moved_and_synced(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('dependencies = ["evalkit-py[otel]==0.2.0", "httpx"]\n')
    calls: list[list[str]] = []
    run_update(client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, server_extra=False)
    assert pyproject.read_text() == f'dependencies = ["evalkit-py[otel]=={REMOTE}", "httpx"]\n'
    assert calls == [["uv", "sync"]]


def test_current_project_pin_is_left_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    (tmp_path / "pyproject.toml").write_text(f'dependencies = ["evalkit-py=={REMOTE}"]\n')
    calls: list[list[str]] = []
    run_update(client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, server_extra=False)
    assert calls == []


def test_vendored_wheel_prints_the_move_to_pypi(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(version, "current", lambda: REMOTE)
    (tmp_path / "pyproject.toml").write_text(
        'dependencies = ["evalkit[otel] @ ./vendor/evalkit-0.1.0-py3-none-any.whl"]\n'
    )
    run_update(client(), prefix=UV_PREFIX, run=lambda c: None, cwd=tmp_path, server_extra=False)
    assert f'uv remove evalkit && uv add "evalkit-py[otel]=={REMOTE}"' in capsys.readouterr().out


def test_reinstall_refreshes_installed_skills(stale, tmp_path):
    skills = tmp_path / ".claude" / "skills"
    skills.mkdir(parents=True)
    (skills / ".evalkit-installed").write_text("evalkit-usage\n")
    calls: list[list[str]] = []
    run_update(client(), prefix=UV_PREFIX, run=calls.append, cwd=tmp_path, server_extra=False)
    cli = str(UV_PREFIX / "bin" / "evalkit")
    assert calls[1] == [cli, "skills", "install", "--target", str(skills.resolve())]


def test_a_project_venv_is_updated_by_its_sync_alone(stale, tmp_path, capsys):
    (tmp_path / "pyproject.toml").write_text('dependencies = ["evalkit-py[otel]==0.3.0.dev57"]\n')
    venv = tmp_path / ".venv"
    venv.mkdir()
    calls: list[list[str]] = []
    run_update(client(), prefix=venv, run=calls.append, cwd=tmp_path, server_extra=False)
    assert calls == [["uv", "sync"]]
    assert f"installed evalkit {REMOTE}" in capsys.readouterr().out
