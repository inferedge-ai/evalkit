from pathlib import Path

import pytest
from pydantic import ValidationError

from evalkit.config import (
    AuthConfig,
    Config,
    LocalConfig,
    MissingEnvError,
    OidcConfig,
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


def test_trusted_endpoints_must_be_http_urls_with_a_host(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    cfg = load_config(write(tmp_path, BASIC + "trusted_endpoints:\n  - http://litellm-svc:4000\n"))
    assert cfg.trusted_endpoints == ["http://litellm-svc:4000"]
    for typo in ("litellm-svc:4000", "ftp://gateway", "http://user@gateway", "http://gw:40OO"):
        with pytest.raises(ValidationError, match="trusted_endpoints"):
            load_config(write(tmp_path, BASIC + f"trusted_endpoints:\n  - {typo}\n"))


def test_validation_errors_never_echo_resolved_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    monkeypatch.setenv("SECRET", "sk-proj-SECRET")
    with pytest.raises(ValidationError) as raised:
        load_config(write(tmp_path, BASIC + "unknown_block:\n  key: env:SECRET\n"))
    assert "sk-proj-SECRET" not in str(raised.value)
    with pytest.raises(ValidationError) as raised:
        load_config(write(tmp_path, BASIC + "otel:\n  settle_seconds: env:SECRET\n"))
    assert "sk-proj-SECRET" not in str(raised.value)
    for url in ("http://u:sk-proj-SECRET@gateway", "http://u:sk-proj-SECRET/x@gateway"):
        monkeypatch.setenv("SECRET_URL", url)
        with pytest.raises(ValidationError) as raised:
            load_config(write(tmp_path, BASIC + "trusted_endpoints:\n  - env:SECRET_URL\n"))
        assert "sk-proj-SECRET" not in str(raised.value)


def test_local_credentials_must_be_plain_env_names(tmp_path):
    for bad in ("env:", "env:NAME:default", "env:1X"):
        (tmp_path / "evalkit.yaml").write_text(f"credentials:\n  agents:\n    a@dev: '{bad}'\n")
        with pytest.raises(ValidationError, match="no defaults"):
            LocalConfig.find(tmp_path)


def test_local_judge_keys_were_removed(tmp_path):
    (tmp_path / "evalkit.yaml").write_text("credentials:\n  llm_providers:\n    http://j: env:K\n")
    with pytest.raises(ValidationError, match="credentials.llm_providers was removed"):
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


def test_local_config_without_cases_or_fixtures_uses_the_defaults(tmp_path):
    (tmp_path / "evalkit.yaml").write_text("")
    nested = tmp_path / "src"
    nested.mkdir()
    cfg = LocalConfig.find(nested)
    assert cfg is not None
    assert cfg.cases == tmp_path / "evalkit" / "cases"
    assert cfg.fixtures == tmp_path / "evalkit" / "fixtures"
    assert cfg.resolve_env().cases == tmp_path / "evalkit" / "cases"


def test_local_config_without_a_file_defaults_to_the_first_start(tmp_path, monkeypatch):
    monkeypatch.delenv("RESEARCH_AGENT_KEY", raising=False)
    cwd = tmp_path / "cwd"
    script_dir = tmp_path / "scripts"
    cwd.mkdir()
    script_dir.mkdir()
    (cwd / ".env").write_text("RESEARCH_AGENT_KEY=from-dotenv\n")
    cfg = LocalConfig.find_or_default([cwd, script_dir])
    assert cfg.path == cwd / "evalkit.yaml"
    assert cfg.cases == cwd / "evalkit" / "cases"
    assert cfg.fixtures == cwd / "evalkit" / "fixtures"
    assert cfg.credentials.agents == {} and cfg.credentials.evaluators == {}
    assert cfg.environ()["RESEARCH_AGENT_KEY"] == "from-dotenv"
    assert cfg.resolve_env().cases == cwd / "evalkit" / "cases"


def test_local_config_file_beside_a_later_start_wins_over_the_default(tmp_path):
    cwd = tmp_path / "cwd"
    script_dir = tmp_path / "scripts"
    cwd.mkdir()
    script_dir.mkdir()
    (script_dir / "evalkit.yaml").write_text("cases: suites\n")
    cfg = LocalConfig.find_or_default([cwd, script_dir])
    assert cfg.path == script_dir / "evalkit.yaml"
    assert cfg.cases == script_dir / "suites"
    assert cfg.fixtures == script_dir / "evalkit" / "fixtures"


def test_the_moved_to_db_hint_names_env_refs_as_shell_variables(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm/v1")
    monkeypatch.setenv("LLM_MODEL", "m1")
    old = (
        "judge_default:\n  base_url: env:LLM_BASE_URL\n  model: env:LLM_MODEL\n"
        "credentials:\n  judges:\n    env:LLM_BASE_URL: env:LLM_API_KEY\n"
    )
    with pytest.raises(ValidationError) as raised:
        load_config(write(tmp_path, BASIC + old))
    message = str(raised.value)
    assert "--base-url $LLM_BASE_URL --api-key-env LLM_API_KEY" in message
    assert "--model m1" in message


def test_db_pool_defaults_and_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    cfg = load_config(write(tmp_path, BASIC))
    assert (cfg.db.pool_size, cfg.db.max_overflow) == (20, 10)
    text = BASIC.replace("db:\n", "db:\n  pool_size: 40\n  max_overflow: 0\n")
    cfg = load_config(write(tmp_path, text))
    assert (cfg.db.pool_size, cfg.db.max_overflow) == (40, 0)


def test_runner_evaluation_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "pk")
    assert load_config(write(tmp_path, BASIC)).runner.evaluation_concurrency == 16
    text = BASIC + "runner:\n  evaluation_concurrency: 4\n"
    assert load_config(write(tmp_path, text)).runner.evaluation_concurrency == 4


def test_a_base_url_with_a_bad_port_is_a_config_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_URL", "postgresql+psycopg://localhost/x")
    text = BASIC.replace("https://evalkit.internal", "http://localhost:80a0")
    with pytest.raises(ValueError, match="base_url"):
        load_config(write(tmp_path, text))


OIDC_BASE = {
    "server": {"base_url": "http://evalkit.test"},
    "db": {"url": "postgresql+psycopg://x/y"},
    "blob": {"url": "file:///tmp/blob"},
}
OIDC = {
    "issuer": "https://idp.test",
    "client_id": "evalkit-ui",
    "audience": "https://evalkit.test",
    "name": "Okta",
}


def test_oidc_config_defaults():
    cfg = Config.model_validate({**OIDC_BASE, "auth": {"oidc": OIDC}})
    assert cfg.auth.oidc == OidcConfig(**OIDC)
    assert cfg.auth.oidc.scopes == ["openid", "profile", "email", "offline_access"]
    assert cfg.auth.oidc.authorize_params == {}
    assert cfg.auth.oidc.algorithms == ["RS256"]
    assert cfg.auth.password_login == "admin"


def test_oidc_scopes_must_include_openid():
    with pytest.raises(ValidationError, match="must include openid"):
        Config.model_validate({**OIDC_BASE, "auth": {"oidc": {**OIDC, "scopes": ["email"]}}})


def test_oidc_required_scopes_must_be_requested():
    scopes = ["openid", "evalkit:read"]
    with pytest.raises(ValidationError, match="required_scopes lists evalkit:read, which"):
        Config.model_validate(
            {**OIDC_BASE, "auth": {"oidc": {**OIDC, "required_scopes": ["evalkit:read"]}}}
        )
    cfg = Config.model_validate(
        {
            **OIDC_BASE,
            "auth": {"oidc": {**OIDC, "scopes": scopes, "required_scopes": ["evalkit:read"]}},
        }
    )
    assert cfg.auth.oidc.required_scopes == ["evalkit:read"]
    cfg = Config.model_validate({**OIDC_BASE, "auth": {"oidc": {**OIDC, "required_scopes": []}}})
    assert cfg.auth.oidc.required_scopes == []


def test_password_login_is_admin_all_or_none():
    with pytest.raises(ValidationError):
        Config.model_validate({**OIDC_BASE, "auth": {"password_login": "nobody"}})
    cfg = Config.model_validate({**OIDC_BASE, "auth": {"oidc": OIDC, "password_login": "all"}})
    assert cfg.auth.resolve_password_login() == "all"


def test_password_login_none_needs_an_idp_that_names_admins():
    for auth in ({"password_login": "none"}, {"oidc": OIDC, "password_login": "none"}):
        with pytest.raises(ValidationError, match="password_login: none needs auth.oidc"):
            Config.model_validate({**OIDC_BASE, "auth": auth})
    admins = {"name": "roles", "value": "evalkit-admin"}
    cfg = Config.model_validate(
        {**OIDC_BASE, "auth": {"oidc": {**OIDC, "admin_claim": admins}, "password_login": "none"}}
    )
    assert cfg.auth.resolve_password_login() == "none"


def test_password_login_is_all_without_an_idp():
    assert AuthConfig().resolve_password_login() == "all"
    assert AuthConfig(password_login="admin").resolve_password_login() == "all"
    assert AuthConfig(oidc=OidcConfig(**OIDC)).resolve_password_login() == "admin"


def test_mcp_oauth_moved_to_auth_oidc():
    with pytest.raises(ValidationError, match="mcp.oauth moved to auth.oidc"):
        Config.model_validate({**OIDC_BASE, "mcp": {"oauth": {"issuer": "x", "jwks_uri": "y"}}})
