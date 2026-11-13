import re
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from evalkit.cli import main as main_module
from evalkit.cli.main import app
from evalkit.core.traces.presets import list_preset_names

SKILLS = Path(__file__).parent.parent / "skills"
# Agent Skills spec limits (agentskills.io/specification), enforced by Claude Code.
NAME_MAX_CHARS = 64
DESCRIPTION_MAX_CHARS = 1024
EXPECTED = {
    "evalkit-deployment",
    "evalkit-usage",
    "authoring-eval-cases",
    "eval-driven-development",
    "connect-traces",
    "getting-started",
}


def test_skills_with_valid_frontmatter():
    found = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
    assert found == EXPECTED
    for skill_md in SKILLS.glob("*/SKILL.md"):
        text = skill_md.read_text()
        assert text.startswith("---\n")
        frontmatter = yaml.safe_load(text.split("---\n")[1])
        assert frontmatter["name"] == skill_md.parent.name
        assert len(frontmatter["name"]) <= NAME_MAX_CHARS
        assert 50 < len(frontmatter["description"]) <= DESCRIPTION_MAX_CHARS


def test_skills_install_copies_all(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["skills", "install", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    installed = {p.parent.name for p in tmp_path.glob("*/SKILL.md")}
    assert installed == EXPECTED
    assert (tmp_path / "eval-driven-development" / "references" / "diagnose.md").is_file()
    assert (tmp_path / "evalkit-docs" / "try-locally.md").is_file()
    assert set((tmp_path / ".evalkit-installed").read_text().split()) == EXPECTED | {"evalkit-docs"}


def test_skills_install_removes_only_what_an_earlier_install_put_there(tmp_path):
    (tmp_path / "retired-skill").mkdir()
    (tmp_path / "retired-skill" / "SKILL.md").write_text("old")
    (tmp_path / "my-own-skill").mkdir()
    (tmp_path / "my-own-skill" / "SKILL.md").write_text("mine")
    (tmp_path / ".evalkit-installed").write_text("retired-skill\nevalkit-usage\n")
    result = CliRunner().invoke(app, ["skills", "install", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "removed retired-skill, which an earlier install put there" in result.output
    assert not (tmp_path / "retired-skill").exists()
    assert (tmp_path / "my-own-skill" / "SKILL.md").read_text() == "mine"


def test_skill_links_to_installed_docs_resolve(tmp_path):
    CliRunner().invoke(app, ["skills", "install", "--target", str(tmp_path)])
    for path in tmp_path.rglob("*.md"):
        for href in re.findall(r"\]\((\.\./(?:\.\./)*evalkit-docs/[^)#\s]+)", path.read_text()):
            assert (path.parent / href).resolve().is_file(), f"{path} links {href}"


def test_connect_traces_ships_no_mapping_json():
    assert not list((SKILLS / "connect-traces").rglob("*.json"))
    assert set(list_preset_names()) >= {"openinference", "gen_ai"}


def test_skills_install_drops_files_a_shipped_skill_no_longer_has(tmp_path):
    stale = tmp_path / "evalkit-usage" / "references" / "retired.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    docs_stale = tmp_path / "evalkit-docs" / "retired-page.md"
    docs_stale.parent.mkdir(parents=True)
    docs_stale.write_text("old")
    (tmp_path / ".evalkit-installed").write_text("evalkit-usage\nevalkit-docs\n")
    result = CliRunner().invoke(app, ["skills", "install", "--target", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert not stale.exists()
    assert not docs_stale.exists()
    assert (tmp_path / "evalkit-usage" / "SKILL.md").is_file()


def test_skills_install_leaves_out_env_files_wheels_and_bytecode(tmp_path, monkeypatch):
    source = tmp_path / "source"
    kit = source / "demo-skill" / "try"
    (kit / "__pycache__").mkdir(parents=True)
    (source / "demo-skill" / "SKILL.md").write_text("demo")
    (kit / ".env").write_text("LLM_API_KEY=secret")
    (kit / ".env.example").write_text("LLM_API_KEY=")
    (kit / "evalkit-0.0.0-py3-none-any.whl").write_text("wheel")
    (kit / "__pycache__" / "agent.cpython-313.pyc").write_text("pyc")
    monkeypatch.setattr(main_module, "skills_root", lambda: source)
    target = tmp_path / "target"
    result = CliRunner().invoke(app, ["skills", "install", "--target", str(target)])
    assert result.exit_code == 0, result.output
    installed = target / "demo-skill" / "try"
    assert sorted(p.name for p in installed.iterdir()) == [".env.example"]


def test_skills_linking_installed_docs_say_where_the_pages_live():
    for skill_md in SKILLS.glob("*/SKILL.md"):
        files = skill_md.parent.rglob("*.md")
        if any("evalkit-docs/" in path.read_text() for path in files):
            assert "docs/user/" in skill_md.read_text(), skill_md.parent.name


def test_skills_install_leaves_docs_images_out(tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    (docs / "images").mkdir(parents=True)
    (docs / "quickstart.md").write_text("![run](images/run.png)")
    (docs / "images" / "run.png").write_bytes(b"png")
    monkeypatch.setattr(main_module, "docs_root", lambda: docs)
    target = tmp_path / "target"
    result = CliRunner().invoke(app, ["skills", "install", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in (target / "evalkit-docs").iterdir()) == ["quickstart.md"]


DEFAULT_FOLDERS = (Path(".agents/skills"), Path(".claude/skills"))


def install_in(repo: Path, monkeypatch) -> str:
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(app, ["skills", "install"])
    assert result.exit_code == 0, result.output
    return result.output


def assert_installed(folder: Path) -> None:
    assert {p.parent.name for p in folder.glob("*/SKILL.md")} == EXPECTED
    assert (folder / "evalkit-docs" / "try-locally.md").is_file()


@pytest.mark.parametrize("existing", [(), (".claude",), (".agents",), (".agents", ".claude")])
def test_skills_install_writes_both_default_folders(tmp_path, monkeypatch, existing):
    for name in existing:
        (tmp_path / name).mkdir()
    output = install_in(tmp_path, monkeypatch)
    for folder in DEFAULT_FOLDERS:
        assert_installed(tmp_path / folder)
    assert output.splitlines() == [
        f"{folder}: wrote {len(EXPECTED)} skills and evalkit-docs/" for folder in DEFAULT_FOLDERS
    ]


def test_skills_install_with_target_writes_only_there(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(app, ["skills", "install", "--target", "custom"])
    assert result.exit_code == 0, result.output
    assert_installed(tmp_path / "custom")
    assert not (tmp_path / ".agents").exists()
    assert not (tmp_path / ".claude").exists()


def test_second_install_names_what_it_replaced_and_removed(tmp_path, monkeypatch):
    install_in(tmp_path, monkeypatch)
    claude = tmp_path / ".claude" / "skills"
    (claude / "retired-skill").mkdir()
    (claude / ".evalkit-installed").write_text(
        (claude / ".evalkit-installed").read_text() + "retired-skill\n"
    )
    (claude / "my-own-skill").mkdir()
    (claude / "my-own-skill" / "SKILL.md").write_text("mine")
    output = install_in(tmp_path, monkeypatch)
    updated = f"updated {len(EXPECTED)} skills and evalkit-docs/"
    assert output.splitlines() == [
        f".agents/skills: {updated}",
        ".claude/skills: removed retired-skill, which an earlier install put there",
        f".claude/skills: {updated}",
    ]
    assert not (claude / "retired-skill").exists()
    assert (claude / "my-own-skill" / "SKILL.md").read_text() == "mine"


def test_install_reports_updated_and_new_skills_apart(tmp_path, monkeypatch):
    install_in(tmp_path, monkeypatch)
    for folder in DEFAULT_FOLDERS:
        shutil.rmtree(tmp_path / folder / "connect-traces")
    output = install_in(tmp_path, monkeypatch)
    assert output.splitlines() == [
        f"{folder}: updated {len(EXPECTED) - 1} skills and evalkit-docs/, wrote 1 skill"
        for folder in DEFAULT_FOLDERS
    ]


def test_describe_installed_counts_skills_without_assuming_the_docs():
    assert main_module.describe_installed(["a", "b"]) == "2 skills"
    assert main_module.describe_installed(["a", "evalkit-docs"]) == "1 skill and evalkit-docs/"
    assert main_module.describe_installed(["evalkit-docs"]) == "evalkit-docs/"


def test_install_never_overwrites_a_skill_evalkit_did_not_install(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    own = tmp_path / ".claude" / "skills" / "connect-traces"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("mine")
    for _ in range(2):
        result = CliRunner().invoke(app, ["skills", "install"])
        assert result.exit_code == 1
        assert (own / "SKILL.md").read_text() == "mine"
        assert sorted(p.name for p in own.iterdir()) == ["SKILL.md"]
        assert (
            ".claude/skills: skipped connect-traces: .claude/skills/connect-traces is already "
            "there and no evalkit install put it there"
        ) in result.output
        assert "did not overwrite .claude/skills/connect-traces." in result.output
        assert "--target DIR" in result.output
    assert_installed(tmp_path / ".agents" / "skills")
    claude = tmp_path / ".claude" / "skills"
    assert {p.parent.name for p in claude.glob("*/SKILL.md")} == EXPECTED
    assert (claude / "evalkit-docs" / "try-locally.md").is_file()
    assert set((claude / ".evalkit-installed").read_text().split()) == (
        EXPECTED - {"connect-traces"}
    ) | {"evalkit-docs"}
    assert f".claude/skills: updated {len(EXPECTED) - 1} skills and evalkit-docs/" in result.output


def test_install_takes_over_once_the_clashing_skill_is_moved_away(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    own = tmp_path / ".claude" / "skills" / "connect-traces"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("mine")
    assert CliRunner().invoke(app, ["skills", "install"]).exit_code == 1
    own.rename(own.with_name("my-connect-traces"))
    output = install_in(tmp_path, monkeypatch)
    assert output.splitlines()[1] == (
        f".claude/skills: updated {len(EXPECTED) - 1} skills and evalkit-docs/, wrote 1 skill"
    )
    claude = tmp_path / ".claude" / "skills"
    assert (claude / "connect-traces" / "SKILL.md").read_text() != "mine"
    assert set((claude / ".evalkit-installed").read_text().split()) == EXPECTED | {"evalkit-docs"}
    assert (claude / "my-connect-traces" / "SKILL.md").read_text() == "mine"


def test_install_does_not_overwrite_a_folder_of_skills_without_a_manifest(tmp_path):
    for name in [*EXPECTED, "evalkit-docs"]:
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text("from before the manifest")
    result = CliRunner().invoke(app, ["skills", "install", "--target", str(tmp_path)])
    assert result.exit_code == 1
    assert f"{tmp_path}: wrote nothing" in result.output
    for name in [*EXPECTED, "evalkit-docs"]:
        assert (tmp_path / name / "SKILL.md").read_text() == "from before the manifest"
        assert f"{tmp_path}: skipped {name}: " in result.output
    assert (tmp_path / ".evalkit-installed").read_text() == ""
