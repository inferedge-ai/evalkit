import re
from pathlib import Path

import yaml
from typer.testing import CliRunner

from evalkit.cli import main as main_module
from evalkit.cli.main import app

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
    assert "removed retired-skill" in result.output
    assert not (tmp_path / "retired-skill").exists()
    assert (tmp_path / "my-own-skill" / "SKILL.md").read_text() == "mine"


def test_skill_links_to_installed_docs_resolve(tmp_path):
    CliRunner().invoke(app, ["skills", "install", "--target", str(tmp_path)])
    for path in tmp_path.rglob("*.md"):
        for href in re.findall(r"\]\((\.\./(?:\.\./)*evalkit-docs/[^)#\s]+)", path.read_text()):
            assert (path.parent / href).resolve().is_file(), f"{path} links {href}"


def test_connect_traces_ships_the_reference_mappings():
    for name in ("openinference.json", "gen_ai.json"):
        assert (SKILLS / "connect-traces" / "references" / name).is_file()
    assert not (
        Path(__file__).parent.parent / "src" / "evalkit" / "core" / "traces" / "mappings"
    ).exists()


def test_skills_install_drops_files_a_shipped_skill_no_longer_has(tmp_path):
    stale = tmp_path / "evalkit-usage" / "references" / "retired.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("old")
    docs_stale = tmp_path / "evalkit-docs" / "retired-page.md"
    docs_stale.parent.mkdir(parents=True)
    docs_stale.write_text("old")
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
