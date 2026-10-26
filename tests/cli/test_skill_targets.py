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


def test_refresh_reinstalls_every_recorded_target(tmp_path, isolated_skill_targets):
    first = installed(tmp_path / "a" / ".claude" / "skills")
    second = installed(tmp_path / "b" / ".agents" / "skills")
    record(first)
    record(second)
    calls: list[list[str]] = []
    refresh(tmp_path, CLI, calls.append)
    assert calls == [
        [str(CLI), "skills", "install", "--target", str(first)],
        [str(CLI), "skills", "install", "--target", str(second)],
    ]


def test_refresh_picks_up_an_unrecorded_install_in_cwd(tmp_path, isolated_skill_targets):
    found = installed(tmp_path / ".claude" / "skills")
    calls: list[list[str]] = []
    refresh(tmp_path, CLI, calls.append)
    assert calls == [[str(CLI), "skills", "install", "--target", str(found)]]
    assert isolated_skill_targets.read_text() == f"{found}\n"


def test_refresh_drops_a_target_without_a_manifest(tmp_path, isolated_skill_targets):
    target = tmp_path / "repo" / ".claude" / "skills"
    target.mkdir(parents=True)
    record(target)
    refresh(tmp_path, CLI, [].append)
    assert isolated_skill_targets.read_text() == ""
