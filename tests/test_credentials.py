import pytest

from evalkit.credentials import (
    CredentialRegistry,
    CredentialsConfig,
    MissingCredential,
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


def test_judge_keys_are_no_longer_read_from_evalkit_yaml():
    for key in ("llm_providers", "judges"):
        with pytest.raises(ValueError, match="--judge-api-key-env"):
            CredentialsConfig.model_validate({key: {"https://x/v1": "env:K"}})


def test_values_must_be_env_refs():
    with pytest.raises(ValueError):
        CredentialsConfig(agents={"a@dev": "plaintext"})
