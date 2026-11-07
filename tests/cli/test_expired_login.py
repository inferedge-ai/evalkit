import httpx2
import pytest
import typer

from evalkit.cli import main as main_module
from evalkit.cli.client import ApiClient
from evalkit.client import connection, oidc
from evalkit.client.oidc import ClientCredentialsRefused, EvalkitAuth, LoginExpired


class ExpiredSource:
    def bearer(self) -> str:
        raise LoginExpired("http://x")


def test_expired_login_becomes_one_red_line(capsys):
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json={}))
    client = ApiClient("http://x", EvalkitAuth(ExpiredSource()), transport=transport)
    with pytest.raises(typer.Exit) as raised:
        client.get("/api/auth/me")
    assert raised.value.exit_code == 1
    assert "run `evalkit login --url http://x`" in capsys.readouterr().err


def test_refused_client_credentials_become_one_red_line(capsys):
    class RefusedSource:
        def bearer(self) -> str:
            raise ClientCredentialsRefused("invalid_client", "bad secret")

    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json={}))
    client = ApiClient("http://x", EvalkitAuth(RefusedSource()), transport=transport)
    with pytest.raises(typer.Exit) as raised:
        client.get("/api/auth/me")
    assert raised.value.exit_code == 1
    assert "client credentials refused by the identity provider: invalid_client" in (
        capsys.readouterr().err
    )


def test_unreachable_server_during_provider_lookup_is_one_red_line(monkeypatch, tmp_path, capsys):
    def unreachable(url, transport=None):
        raise httpx2.ConnectError("refused")

    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setenv("EVALKIT_SERVER_URL", "http://x")
    monkeypatch.setenv("EVALKIT_OIDC_CLIENT_ID", "ci")
    monkeypatch.setenv("EVALKIT_OIDC_CLIENT_SECRET", "s3cret")
    monkeypatch.delenv("EVALKIT_TOKEN", raising=False)
    monkeypatch.setattr(oidc, "fetch_providers", unreachable)
    with pytest.raises(typer.Exit) as raised:
        main_module._client()
    assert raised.value.exit_code == 1
    assert capsys.readouterr().err.strip() == "refused"
