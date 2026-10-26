from pathlib import Path

import pytest
from pydantic import ValidationError

from evalkit.config import (
    LocalConfig,
    MissingEnvError,
    RunnerConfig,
    load_config,
)
from evalkit.credentials import CredentialRegistry

BASIC = """
server:
  base_url: https://evalkit.internal
db:
  url: env:DB_URL
blob:
  url: file:///data/evalkit
judge_default:
  base_url: https://llm.internal
  model: vertex_ai/gemini-3.6-flash
credentials:
  agents:
    ouragent@dev: env:OURAGENT_KEY
  judges:
    https://llm.internal: env:JUDGE_KEY
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return p


def test_env_indirection(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    cfg = load_config(write(tmp_path, BASIC))
    assert cfg.db.url == "pk"


def test_missing_env_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("DB_URL", raising=False)
    path = write(tmp_path, BASIC)
    with pytest.raises(MissingEnvError) as missing:
        load_config(path)
    assert str(missing.value) == (f"{path}: db.url needs env DB_URL (or env:DB_URL:<default>)")


def test_credentials_stay_env_refs_until_used(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    monkeypatch.delenv("OURAGENT_KEY", raising=False)
    cfg = load_config(write(tmp_path, BASIC))
    assert cfg.credentials.agents == {"ouragent@dev": "env:OURAGENT_KEY"}
    registry = CredentialRegistry(cfg.credentials, {"JUDGE_KEY": "jk"})
    assert registry.require_judge_key("https://llm.internal") == "jk"


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    cfg = load_config(write(tmp_path, BASIC))
    assert cfg.judge_default.reasoning_effort == "high"
    assert cfg.judge_default.max_tokens == 32768
    assert cfg.judge_default.concurrency == 16
    assert cfg.auth.token_ttl_hours == 720
    assert cfg.cost_map.source == "bundled"
    assert cfg.encryption_key is None
    assert isinstance(cfg, RunnerConfig)


def test_validation_errors_never_echo_resolved_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    monkeypatch.setenv("SECRET", "sk-proj-SECRET")
    with pytest.raises(ValidationError) as raised:
        load_config(write(tmp_path, BASIC + "unknown_block:\n  key: env:SECRET\n"))
    assert "sk-proj-SECRET" not in str(raised.value)
    with pytest.raises(ValidationError) as raised:
        load_config(write(tmp_path, BASIC + "otel:\n  settle_seconds: env:SECRET\n"))
    assert "sk-proj-SECRET" not in str(raised.value)


def test_credentials_must_be_plain_env_names(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    for bad in ("env:", "env:NAME:default", "env:1X"):
        text = BASIC + f"credentials:\n  agents:\n    a@dev: '{bad}'\n"
        with pytest.raises(ValidationError, match="no defaults"):
            load_config(write(tmp_path, text))


def test_api_keys_on_entries_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    judge_key = BASIC.replace("  model: vertex_ai", "  api_key: k\n  model: vertex_ai")
    with pytest.raises(ValidationError):
        load_config(write(tmp_path, judge_key))


def test_example_config_loads(monkeypatch):
    for var in ["EVALKIT_DATABASE_URL", "EVALKIT_ENCRYPTION_KEY"]:
        monkeypatch.setenv(var, "x")
    example = Path(__file__).parent.parent / "skills/evalkit-deployment/config.example.yaml"
    cfg = load_config(example)
    assert cfg.credentials.agents == {} and cfg.credentials.evaluators == {}
    assert cfg.credentials.judges == {cfg.judge_default.base_url: "env:JUDGE_LLM_API_KEY"}


def test_accent_color(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    cfg = load_config(write(tmp_path, BASIC))
    assert cfg.server.accent_color is None
    cfg = load_config(
        write(tmp_path, BASIC.replace("server:\n", "server:\n  accent_color: '#dc2626'\n"))
    )
    assert cfg.server.accent_color == "#dc2626"


def test_custom_suites_dir_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    with pytest.raises(ValidationError, match="custom_suites_dir"):
        load_config(write(tmp_path, BASIC + "custom_suites_dir: ./cases\n"))


def test_server_config_reads_otel_and_encryption_key(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    monkeypatch.setenv("ENC", "k")
    text = BASIC + "otel:\n  settle_seconds: 3\nencryption_key: env:ENC\n"
    cfg = load_config(write(tmp_path, text))
    assert cfg.otel.settle_seconds == 3 and cfg.otel.max_batch_bytes == 16 * 1024 * 1024
    assert cfg.encryption_key == "k"


LOCAL = """
cases: eval/cases
fixtures: eval/fixtures
credentials:
  agents:
    research-agent: env:RESEARCH_AGENT_KEY
  judges:
    http://litellm.local: env:LITELLM_KEY
"""


def test_local_config_resolves_paths(tmp_path):
    (tmp_path / "evalkit.yaml").write_text(LOCAL)
    nested = tmp_path / "eval" / "cases" / "finance"
    nested.mkdir(parents=True)
    cfg = LocalConfig.find(nested)
    assert cfg is not None
    assert cfg.path == tmp_path / "evalkit.yaml"
    assert cfg.cases == tmp_path / "eval" / "cases"
    assert cfg.fixtures == tmp_path / "eval" / "fixtures"


def test_local_credentials_read_dotenv_beside_evalkit_yaml(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCH_AGENT_KEY", raising=False)
    (tmp_path / "evalkit.yaml").write_text(LOCAL)
    (tmp_path / ".env").write_text("RESEARCH_AGENT_KEY=from-dotenv\n")
    cfg = LocalConfig.find(tmp_path)
    assert cfg is not None
    registry = CredentialRegistry(cfg.credentials, cfg.environ(), home="akshat")
    assert registry.key_for("agent", "research-agent@akshat") == "from-dotenv"
    monkeypatch.setenv("RESEARCH_AGENT_KEY", "from-env")
    registry = CredentialRegistry(cfg.credentials, cfg.environ(), home="akshat")
    assert registry.key_for("agent", "research-agent@akshat") == "from-env"


def test_env_reference_with_default(tmp_path, monkeypatch):
    monkeypatch.delenv("EVALKIT_COST_MAP", raising=False)
    local = LOCAL + "cost_map:\n  source: env:EVALKIT_COST_MAP:bundled\n"
    (tmp_path / "evalkit.yaml").write_text(local)
    cfg = LocalConfig.find(tmp_path)
    assert cfg is not None
    assert cfg.resolve_env(scoring=True).cost_map.source == "bundled"
    assert cfg.resolve_env(scoring=False).cost_map.source == "env:EVALKIT_COST_MAP:bundled"
    monkeypatch.setenv("EVALKIT_COST_MAP", "/tmp/costs.json")
    assert cfg.resolve_env(scoring=True).cost_map.source == "/tmp/costs.json"


def test_local_config_absent(tmp_path):
    assert LocalConfig.find(tmp_path) is None


def test_local_config_without_cases_or_fixtures(tmp_path):
    (tmp_path / "evalkit.yaml").write_text("")
    cfg = LocalConfig.find(tmp_path)
    assert cfg is not None
    assert cfg.cases is None and cfg.fixtures is None
    assert cfg.resolve_env().cases is None
