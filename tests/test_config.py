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
"""
OLD_KEYS = """judge_default:
  base_url: https://llm.internal
  model: vertex_ai/gemini-3.6-flash
credentials:
  agents:
    ouragent@dev: env:OURAGENT_KEY
  evaluators:
    rtc@dev: env:RTC_KEY
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


def test_server_keys_and_judge_moved_to_the_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    with pytest.raises(ValidationError) as raised:
        load_config(write(tmp_path, BASIC + OLD_KEYS))
    message = str(raised.value)
    assert "credentials and judge_default moved to the database" in message
    assert (
        "evalkit llm-providers create <name> --base-url https://llm.internal "
        "--api-key-env JUDGE_KEY" in message
    )
    assert "evalkit judge-default set --provider <name> --model vertex_ai/gemini-3.6-flash" in (
        message
    )
    assert "evalkit agents edit ouragent@dev --api-key-env OURAGENT_KEY" in message
    assert "evalkit evaluators edit rtc@dev --api-key-env RTC_KEY" in message


def test_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    cfg = load_config(write(tmp_path, BASIC))
    assert cfg.allow_private_endpoints is False
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


def test_local_credentials_must_be_plain_env_names(tmp_path):
    for bad in ("env:", "env:NAME:default", "env:1X"):
        (tmp_path / "evalkit.yaml").write_text(f"credentials:\n  agents:\n    a@dev: '{bad}'\n")
        with pytest.raises(ValidationError, match="no defaults"):
            LocalConfig.find(tmp_path)


def test_local_judges_map_was_renamed(tmp_path):
    (tmp_path / "evalkit.yaml").write_text("credentials:\n  judges:\n    http://j: env:K\n")
    with pytest.raises(ValidationError, match="now credentials.llm_providers"):
        LocalConfig.find(tmp_path)


def test_example_config_loads(monkeypatch):
    for var in ["EVALKIT_DATABASE_URL", "EVALKIT_ENCRYPTION_KEY"]:
        monkeypatch.setenv(var, "x")
    example = Path(__file__).parent.parent / "skills/evalkit-deployment/config.example.yaml"
    assert load_config(example).encryption_key == "x"


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


def test_server_config_reads_sentry(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    monkeypatch.setenv("SENTRY_DSN", "https://key@o1.ingest.sentry.io/1")
    assert load_config(write(tmp_path, BASIC)).sentry is None
    cfg = load_config(write(tmp_path, BASIC + "sentry:\n  dsn: env:SENTRY_DSN\n"))
    assert cfg.sentry is not None
    assert cfg.sentry.dsn == "https://key@o1.ingest.sentry.io/1"
    assert cfg.sentry.traces_sample_rate == 0.0
    assert cfg.sentry.max_request_body_size == "never"


LOCAL = """
cases: eval/cases
fixtures: eval/fixtures
credentials:
  agents:
    research-agent: env:RESEARCH_AGENT_KEY
  llm_providers:
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
