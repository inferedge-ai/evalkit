import os
import shutil
from pathlib import Path

import pytest

from evalkit.cli.main import _server_config

KIT_CONFIG = Path(__file__).parents[2] / "skills" / "evalkit-deployment" / "try" / "config.yaml"


@pytest.fixture
def clean_env(monkeypatch):
    for name in ("EVALKIT_ENCRYPTION_KEY", "EVALKIT_ADMIN_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    yield
    for name in ("EVALKIT_ENCRYPTION_KEY", "EVALKIT_ADMIN_PASSWORD"):
        os.environ.pop(name, None)


def test_server_config_reads_the_env_file_beside_it(tmp_path, clean_env):
    shutil.copy(KIT_CONFIG, tmp_path / "config.yaml")
    (tmp_path / ".env").write_text("EVALKIT_ENCRYPTION_KEY=from-file\nEVALKIT_ADMIN_PASSWORD=pw\n")
    cfg = _server_config(tmp_path / "config.yaml")
    assert cfg.encryption_key == "from-file"
    assert os.environ["EVALKIT_ADMIN_PASSWORD"] == "pw"


def test_the_environment_wins_over_the_env_file(tmp_path, clean_env, monkeypatch):
    shutil.copy(KIT_CONFIG, tmp_path / "config.yaml")
    (tmp_path / ".env").write_text("EVALKIT_ENCRYPTION_KEY=from-file\n")
    monkeypatch.setenv("EVALKIT_ENCRYPTION_KEY", "from-env")
    assert _server_config(tmp_path / "config.yaml").encryption_key == "from-env"
