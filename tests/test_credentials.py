import pytest

from evalkit.credentials import (
    CredentialRegistry,
    CredentialsConfig,
    MissingCredential,
    load_dotenv_beside,
)


def test_agent_key_resolves_env_at_lookup():
    reg = CredentialRegistry(
        CredentialsConfig(agents={"ouragent@dev": "env:OURAGENT_KEY"}),
        {"OURAGENT_KEY": "s3cret"},
    )
    assert reg.key_for("agent", "ouragent@dev") == "s3cret"
    assert reg.key_for("agent", "other@dev") is None


def test_bare_name_means_the_home_workspace():
    cfg = CredentialsConfig(agents={"myagent": "env:MINE"})
    reg = CredentialRegistry(cfg, {"MINE": "k"}, home="akshat")
    assert reg.key_for("agent", "myagent@akshat") == "k"
    assert reg.key_for("agent", "myagent@dev") is None
    assert CredentialRegistry(cfg, {"MINE": "k"}).key_for("agent", "myagent@akshat") is None


def test_missing_env_var_fails_fast():
    reg = CredentialRegistry(CredentialsConfig(agents={"a@dev": "env:NOPE"}), {})
    with pytest.raises(MissingCredential, match="NOPE"):
        reg.key_for("agent", "a@dev")


def test_judge_key_required():
    reg = CredentialRegistry(CredentialsConfig(), {})
    with pytest.raises(MissingCredential, match="https://api.example/v1"):
        reg.require_judge_key("https://api.example/v1")


def test_env_beats_dotenv(tmp_path, monkeypatch):
    (tmp_path / "evalkit.yaml").write_text("cases: cases\n")
    (tmp_path / ".env").write_text("A=from_file\nB=only_file\n")
    monkeypatch.setenv("A", "from_env")
    env = load_dotenv_beside(tmp_path / "evalkit.yaml")
    assert env["A"] == "from_env" and env["B"] == "only_file"


def test_values_must_be_env_refs():
    with pytest.raises(ValueError):
        CredentialsConfig(agents={"a@dev": "plaintext"})


def test_judge_key_may_name_its_base_url_by_env():
    cfg = CredentialsConfig(judges={"env:LLM_BASE_URL": "env:LLM_API_KEY"})
    reg = CredentialRegistry(cfg, {"LLM_BASE_URL": "https://llm.example/v1", "LLM_API_KEY": "k"})
    assert reg.require_judge_key("https://llm.example/v1") == "k"
    with pytest.raises(
        MissingCredential, match=r"no credentials.judges entry .*unset: LLM_BASE_URL"
    ):
        CredentialRegistry(cfg, {"LLM_API_KEY": "k"}).require_judge_key("https://x/v1")
    assert CredentialRegistry(cfg, {}).key_for("agent", "a@dev") is None


def test_judge_key_env_refs_must_be_plain_names():
    with pytest.raises(ValueError, match="base URL or env:NAME"):
        CredentialsConfig(judges={"env:LLM_BASE_URL:https://x": "env:K"})


def test_an_unset_env_judge_key_does_not_hide_a_plain_url_entry():
    cfg = CredentialsConfig(
        judges={
            "https://api.openai.com/v1": "env:OPENAI_KEY",
            "env:LLM_BASE_URL": "env:LLM_API_KEY",
        }
    )
    reg = CredentialRegistry(cfg, {"OPENAI_KEY": "o"})
    assert reg.require_judge_key("https://api.openai.com/v1") == "o"
