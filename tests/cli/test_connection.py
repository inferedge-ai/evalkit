from datetime import UTC, datetime, timedelta

import pytest

from evalkit.client import connection
from evalkit.client.connection import Connection, MachineLogin, OidcLogin


def test_save_replaces_the_stored_login(tmp_path):
    path = tmp_path / "config.toml"
    connection.save(Connection("https://dev", "t1", "akshat"), path=path)
    connection.save(Connection("https://prod", "t2", "akshat"), path=path)
    assert connection.load(path) == Connection("https://prod", "t2", "akshat")
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_save_creates_a_private_directory_and_file(tmp_path):
    path = tmp_path / ".evalkit" / "config.toml"
    connection.save(Connection("https://dev", "t1", "akshat"), path=path)
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_save_tightens_an_existing_open_directory_and_file(tmp_path):
    path = tmp_path / ".evalkit" / "config.toml"
    path.parent.mkdir(mode=0o755)
    path.write_text("")
    path.chmod(0o644)
    connection.save(Connection("https://dev", "t1", "akshat"), path=path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(path.parent.stat().st_mode & 0o777) == "0o700"
    assert [p.name for p in path.parent.iterdir()] == ["config.toml"]


def test_old_profiles_file_is_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('default = "dev"\n\n[profiles.dev]\nurl = "https://dev"\n')
    with pytest.raises(LookupError, match="no longer supports"):
        connection.load(path)


def test_active_prefers_env_over_the_file(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    connection.save(Connection("https://dev", "t1", "a"), path=path)
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    monkeypatch.delenv("EVALKIT_TOKEN", raising=False)
    assert connection.active(path=path) == Connection("https://dev", "t1", "a")
    monkeypatch.setenv("EVALKIT_SERVER_URL", "https://env")
    monkeypatch.setenv("EVALKIT_TOKEN", "envtok")
    assert connection.active(path=path) == Connection("https://env", "envtok", None, from_env=True)


def test_active_without_anything_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    monkeypatch.delenv("EVALKIT_TOKEN", raising=False)
    with pytest.raises(LookupError, match="`evalkit login --url <url>` for another server"):
        connection.active(path=tmp_path / "missing.toml")


def test_token_expiry_warning_within_a_day():
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    dev = Connection("https://dev", "t", "akshat")
    later = (now + timedelta(days=3)).isoformat()
    soon = (now + timedelta(hours=5)).isoformat()
    assert connection.token_expiry_warning(dev, None, now) is None
    assert connection.token_expiry_warning(dev, later, now) is None
    assert connection.token_expiry_warning(dev, soon, now) == (
        "the token for https://dev expires in 5.0h; "
        "run `evalkit login --url https://dev` before a long run"
    )
    env = Connection("https://dev", "t", None, from_env=True)
    assert "EVALKIT_TOKEN" in (connection.token_expiry_warning(env, soon, now) or "")


def test_token_expiry_warning_skips_logins_that_renew_themselves():
    now = datetime(2026, 10, 11, 12, tzinfo=UTC)
    soon = (now + timedelta(minutes=30)).isoformat()
    sso = Connection("https://dev", None, "ann", oidc=LOGIN)
    machine = Connection("https://dev", None, None, machine=MachineLogin("id", "secret"))
    password = Connection("https://dev", "t", "ann")
    assert connection.token_expiry_warning(sso, soon, now) is None
    assert connection.token_expiry_warning(machine, soon, now) is None
    assert connection.token_expiry_warning(password, soon, now) is not None


LOGIN = OidcLogin(
    issuer="https://idp.test",
    client_id="evalkit-ui",
    token_endpoint="https://idp.test/token",
    access_token="at-1",
    refresh_token="rt-1",
    expires_at=datetime(2026, 11, 4, 12, 0, tzinfo=UTC),
)


def test_save_and_load_an_oidc_login(tmp_path):
    path = tmp_path / "config.toml"
    connection.save(Connection("https://dev", None, "ann", oidc=LOGIN), path=path)
    text = path.read_text()
    assert "[oidc]" in text and 'refresh_token = "rt-1"' in text
    assert "token = " not in text.split("[oidc]")[0]
    assert connection.load(path) == Connection("https://dev", None, "ann", oidc=LOGIN)


def test_active_prefers_client_credentials_from_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    connection.save(Connection("https://dev", "t1", "a"), path=path)
    monkeypatch.setenv("EVALKIT_SERVER_URL", "https://env")
    monkeypatch.setenv("EVALKIT_TOKEN", "envtok")
    monkeypatch.setenv("EVALKIT_OIDC_CLIENT_ID", "ci")
    monkeypatch.setenv("EVALKIT_OIDC_CLIENT_SECRET", "s3cret")
    active = connection.active(path=path)
    assert active == Connection(
        "https://env", None, None, from_env=True, machine=MachineLogin("ci", "s3cret")
    )
    monkeypatch.delenv("EVALKIT_OIDC_CLIENT_SECRET")
    monkeypatch.delenv("EVALKIT_OIDC_CLIENT_ID")
    assert connection.active(path=path) == Connection("https://env", "envtok", None, from_env=True)


@pytest.mark.parametrize(
    "present,missing",
    [
        ("EVALKIT_OIDC_CLIENT_ID", "EVALKIT_OIDC_CLIENT_SECRET"),
        ("EVALKIT_OIDC_CLIENT_SECRET", "EVALKIT_OIDC_CLIENT_ID"),
    ],
)
def test_active_refuses_half_of_the_client_credentials(tmp_path, monkeypatch, present, missing):
    monkeypatch.setenv("EVALKIT_SERVER_URL", "https://env")
    monkeypatch.setenv("EVALKIT_TOKEN", "envtok")
    monkeypatch.delenv("EVALKIT_OIDC_CLIENT_ID", raising=False)
    monkeypatch.delenv("EVALKIT_OIDC_CLIENT_SECRET", raising=False)
    monkeypatch.setenv(present, "x")
    with pytest.raises(LookupError, match=missing):
        connection.active(path=tmp_path / "config.toml")
