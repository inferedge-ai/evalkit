import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from evalkit import version
from evalkit.cli import init_server, server_commands
from evalkit.cli.client import ApiError
from evalkit.cli.main import _server_config, app
from tests.pg import _admin, _with_database

ROOT = Path(__file__).parents[2]
LLM_FLAGS = ["--llm-base-url", "https://llm.test/v1", "--llm-model", "m", "--llm-api-key", "k"]
ENV_NAMES = ("EVALKIT_DATABASE_URL", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (*ENV_NAMES, "EVALKIT_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)


def test_a_flag_beats_the_env_which_beats_the_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://env.test/v1")
    answers = init_server.resolve(
        {"llm_base_url": "https://flag.test/v1", "llm_api_key": "k"}, yes=True
    )
    assert answers.llm_base_url == "https://flag.test/v1"
    assert answers.llm_model == "env-model"
    assert answers.database_url == init_server.DEFAULT_DATABASE_URL
    assert answers.admin_password == init_server.DEFAULT_ADMIN_PASSWORD


def test_yes_fails_on_a_value_without_a_default():
    with pytest.raises(ApiError, match="--llm-base-url is required with --yes"):
        init_server.resolve({}, yes=True)


def test_missing_values_are_prompted(tmp_path, monkeypatch):
    monkeypatch.setattr(server_commands, "init_database", lambda url: None)
    result = CliRunner().invoke(
        app,
        ["init-server", "--dir", str(tmp_path / "kit")],
        input="\nhttps://llm.test/v1\nm\nkey\n\n",
    )
    assert result.exit_code == 0, result.output
    env = (tmp_path / "kit" / ".env").read_text()
    assert "LLM_BASE_URL=https://llm.test/v1\n" in env
    assert "LLM_API_KEY=key\n" in env
    assert f"EVALKIT_ADMIN_PASSWORD={init_server.DEFAULT_ADMIN_PASSWORD}\n" in env
    assert "Postgres URL: where evalkit stores" in result.output
    assert "LLM API key: kept in .env" in result.output


def test_kit_only_copies_the_kit_and_pins_run_py(tmp_path):
    result = CliRunner().invoke(app, ["init-server", "--kit-only", "--dir", str(tmp_path / "k")])
    assert result.exit_code == 0, result.output
    kit = tmp_path / "k"
    assert (kit / "agent.py").is_file() and (kit / "cases" / "acme").is_dir()
    assert not (kit / ".env").exists()
    assert (kit / "docker-compose.yml").is_file()
    assert f'dependencies = ["{init_server.pinned_requirement()}"]' in (kit / "run.py").read_text()


def test_the_pin_follows_an_editable_checkout():
    assert init_server.pinned_requirement() == f"evalkit-py[otel] @ {ROOT.resolve().as_uri()}"


def test_the_pin_follows_a_release(monkeypatch):
    monkeypatch.setattr(init_server, "_direct_url", lambda: {})
    monkeypatch.setattr(version, "current", lambda: "0.3.0.dev57")
    assert init_server.pinned_requirement() == "evalkit-py[otel]==0.3.0.dev57"


def test_an_existing_config_is_kept_without_force(tmp_path, monkeypatch):
    monkeypatch.setattr(server_commands, "init_database", lambda url: None)
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "config.yaml").write_text("mine: true\n")
    result = CliRunner().invoke(app, ["init-server", "--dir", str(kit), "--yes", *LLM_FLAGS])
    assert result.exit_code == 1
    assert "pass --force" in result.output
    assert (kit / "config.yaml").read_text() == "mine: true\n"


def test_init_server_creates_and_migrates_the_database(tmp_path, pg_server_url):
    name = f"init_{uuid.uuid4().hex[:12]}"
    url = _with_database(pg_server_url, name)
    kit = tmp_path / "kit"
    result = CliRunner().invoke(
        app, ["init-server", "--dir", str(kit), "--database-url", url, "--yes", *LLM_FLAGS]
    )
    assert result.exit_code == 0, result.output
    engine = create_engine(url)
    with engine.connect() as conn:
        assert conn.execute(text("select version_num from alembic_version")).scalar()
    engine.dispose()
    _admin(pg_server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')

    cfg = _server_config(kit / "config.yaml")
    assert cfg.db.url == url
    assert cfg.db.migrate_on_start is False
    assert cfg.blob.url == (kit / "data").resolve().as_uri()
    assert cfg.judge_default.base_url == "https://llm.test/v1"
    assert (kit / ".env").stat().st_mode & 0o777 == 0o600
    assert not (kit / "docker-compose.yml").exists()
    assert not (kit / ".env.example").exists()
    assert "evalkit serve" in result.output
    assert "creates the database named in the Postgres URL" in result.output
    assert "created the database and migrated it" in result.output


def test_direct_url_reads_installed_metadata():
    assert json.dumps(init_server._direct_url())


def test_an_existing_kit_is_kept_without_force(tmp_path):
    kit = tmp_path / "kit"
    kit.mkdir()
    (kit / "run.py").write_text("mine\n")
    result = CliRunner().invoke(app, ["init-server", "--kit-only", "--dir", str(kit)])
    assert result.exit_code == 1
    assert "already holds a try kit (run.py)" in result.output
    assert (kit / "run.py").read_text() == "mine\n"
    forced = CliRunner().invoke(app, ["init-server", "--kit-only", "--force", "--dir", str(kit)])
    assert forced.exit_code == 0
    assert (kit / "run.py").read_text() != "mine\n"
