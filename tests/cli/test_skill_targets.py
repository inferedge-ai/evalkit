from pathlib import Path

from typer.testing import CliRunner

from evalkit.cli import skill_targets
from evalkit.cli.main import app
from evalkit.cli.skill_targets import INSTALL_MANIFEST, record, refresh

CLI = Path("/home/x/.local/share/uv/tools/evalkit/bin/evalkit")


def installed(target: Path) -> Path:
    target.mkdir(parents=True)
    (target / INSTALL_MANIFEST).write_text("evalkit-usage\n")
    return target.resolve()


def test_skills_install_records_its_target_once(tmp_path):
    for _ in range(2):
        result = CliRunner().invoke(app, ["skills", "install", "--target", str(tmp_path / "s")])
        assert result.exit_code == 0, result.output
    assert skill_targets.TARGETS_PATH.read_text() == f"{(tmp_path / 's').resolve()}\n"


def install_call(target: Path) -> list[str]:
    return [str(CLI), "skills", "install", "--target", str(target)]


def test_skills_install_without_target_records_both_default_folders(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["skills", "install"])
    assert result.exit_code == 0, result.output
    agents = (tmp_path / ".agents" / "skills").resolve()
    claude = (tmp_path / ".claude" / "skills").resolve()
    assert skill_targets.TARGETS_PATH.read_text() == f"{agents}\n{claude}\n"


def test_refresh_reinstalls_every_recorded_target(tmp_path, isolated_skill_targets):
    first = installed(tmp_path / "a" / "skills")
    second = installed(tmp_path / "b" / "skills")
    record(first)
    record(second)
    calls: list[list[str]] = []
    refresh(tmp_path, CLI, calls.append)
    assert calls == [install_call(first), install_call(second)]


def test_refresh_refreshes_both_default_folders(tmp_path, isolated_skill_targets):
    agents = installed(tmp_path / "repo" / ".agents" / "skills")
    claude = installed(tmp_path / "repo" / ".claude" / "skills")
    record(agents)
    record(claude)
    calls: list[list[str]] = []
    refresh(tmp_path, CLI, calls.append)
    assert calls == [install_call(agents), install_call(claude)]
    assert isolated_skill_targets.read_text() == f"{agents}\n{claude}\n"


def test_refresh_adds_the_missing_default_folder_to_an_older_install(
    tmp_path, isolated_skill_targets
):
    claude = installed(tmp_path / "repo" / ".claude" / "skills")
    agents = (tmp_path / "repo" / ".agents" / "skills").resolve()
    record(claude)
    calls: list[list[str]] = []
    refresh(tmp_path, CLI, calls.append)
    assert calls == [install_call(agents), install_call(claude)]
    assert isolated_skill_targets.read_text() == f"{agents}\n{claude}\n"


def test_refresh_picks_up_an_unrecorded_install_in_cwd(tmp_path, isolated_skill_targets):
    agents = installed(tmp_path / ".agents" / "skills")
    claude = (tmp_path / ".claude" / "skills").resolve()
    calls: list[list[str]] = []
    refresh(tmp_path, CLI, calls.append)
    assert calls == [install_call(agents), install_call(claude)]
    assert isolated_skill_targets.read_text() == f"{agents}\n{claude}\n"


def test_refresh_carries_on_past_a_failed_install_and_returns_it(tmp_path, isolated_skill_targets):
    first = installed(tmp_path / "a" / "skills")
    second = installed(tmp_path / "b" / "skills")
    record(first)
    record(second)
    calls: list[list[str]] = []

    def run(command: list[str]) -> int:
        calls.append(command)
        return 1 if command[-1] == str(first) else 0

    assert refresh(tmp_path, CLI, run) == [first]
    assert calls == [install_call(first), install_call(second)]
    assert isolated_skill_targets.read_text() == f"{first}\n{second}\n"


def test_refresh_drops_a_target_without_a_manifest(tmp_path, isolated_skill_targets):
    target = tmp_path / "repo" / ".claude" / "skills"
    target.mkdir(parents=True)
    record(target)
    refresh(tmp_path, CLI, [].append)
    assert isolated_skill_targets.read_text() == ""
