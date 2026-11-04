import httpx
import pytest
import typer

from evalkit.cli.client import ApiClient
from evalkit.client.oidc import EvalkitAuth, LoginExpired


class ExpiredSource:
    def bearer(self) -> str:
        raise LoginExpired("http://x")


def test_expired_login_becomes_one_red_line(capsys):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    client = ApiClient("http://x", EvalkitAuth(ExpiredSource()), transport=transport)
    with pytest.raises(typer.Exit) as raised:
        client.get("/api/auth/me")
    assert raised.value.exit_code == 1
    assert "run `evalkit login --url http://x`" in capsys.readouterr().err
