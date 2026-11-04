import threading
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest

from evalkit.client import connection
from evalkit.client.connection import Connection, MachineLogin, OidcLogin
from evalkit.client.oidc import (
    ClientCredentials,
    EvalkitAuth,
    LoginExpired,
    StaticToken,
    StoredOidcLogin,
    build_auth,
    static_auth,
)
from tests.identity import FakeIdp


def stored(idp: FakeIdp, tmp_path, *, seconds_left: int) -> tuple[Connection, object]:
    idp.next_login = ("sub-1", "ann@example.com", "ann")
    tokens = idp._issue(idp._login())
    login = OidcLogin(
        issuer=idp.issuer,
        client_id=idp.client_id,
        token_endpoint=f"{idp.issuer}/token",
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        expires_at=datetime.now(UTC) + timedelta(seconds=seconds_left),
    )
    conn = Connection("https://evalkit.test", None, "ann", oidc=login)
    path = tmp_path / "config.toml"
    connection.save(conn, path=path)
    return conn, path


def test_static_token():
    assert StaticToken("abc").bearer() == "abc"


def test_stored_login_returns_the_access_token_while_fresh(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=3600)
    source = StoredOidcLogin(conn, path=path, transport=idp.transport())
    assert source.bearer() == conn.oidc.access_token
    assert connection.load(path).oidc.refresh_token == conn.oidc.refresh_token


def test_stored_login_refreshes_inside_the_last_minute_and_rewrites_the_file(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    source = StoredOidcLogin(conn, path=path, transport=idp.transport())
    token = source.bearer()
    assert token != conn.oidc.access_token
    saved = connection.load(path).oidc
    assert saved.access_token == token and saved.refresh_token != conn.oidc.refresh_token
    assert saved.expires_at > datetime.now(UTC) + timedelta(seconds=3000)
    assert jwt.decode(token, options={"verify_signature": False})["sub"] == "sub-1"


def test_stored_login_adopts_tokens_another_process_saved(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    other = StoredOidcLogin(conn, path=path, transport=idp.transport())
    rotated = other.bearer()
    stale = StoredOidcLogin(conn, path=path, transport=idp.transport())
    assert stale.bearer() == rotated


def test_stored_login_raises_when_nothing_newer_exists(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    idp.refresh_tokens.clear()
    source = StoredOidcLogin(conn, path=path, transport=idp.transport())
    with pytest.raises(LoginExpired, match="run `evalkit login --url https://evalkit.test`"):
        source.bearer()


def test_stored_login_surfaces_an_idp_outage_instead_of_expiry(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    outage = httpx.MockTransport(lambda request: httpx.Response(503))
    source = StoredOidcLogin(conn, path=path, transport=outage)
    with pytest.raises(httpx.HTTPStatusError):
        source.bearer()


def test_stored_login_does_not_adopt_a_login_for_another_server(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    other = StoredOidcLogin(conn, path=path, transport=idp.transport())
    rotated = other.bearer()
    saved = connection.load(path)
    connection.save(saved._replace(url="https://other.test"), path=path)
    idp.refresh_tokens.clear()
    stale = StoredOidcLogin(conn, path=path, transport=idp.transport())
    assert rotated
    with pytest.raises(LoginExpired):
        stale.bearer()


def test_stored_login_refreshes_once_under_concurrency(tmp_path):
    idp = FakeIdp()
    conn, path = stored(idp, tmp_path, seconds_left=30)
    source = StoredOidcLogin(conn, path=path, transport=idp.transport())
    results: list[str] = []
    threads = [threading.Thread(target=lambda: results.append(source.bearer())) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(set(results)) == 1


def test_client_credentials_fetches_once_and_keeps_it_in_memory(tmp_path):
    idp = FakeIdp()
    idp.machine_clients["ci"] = ("s3cret", "machine-sub")
    source = ClientCredentials(
        MachineLogin("ci", "s3cret"), f"{idp.issuer}/token", {}, transport=idp.transport()
    )
    first = source.bearer()
    assert source.bearer() == first
    assert jwt.decode(first, options={"verify_signature": False})["sub"] == "machine-sub"
    source.expires_at = datetime.now(UTC)
    assert source.bearer() != first
    assert not (tmp_path / "config.toml").exists()


def test_evalkit_auth_sets_the_header_per_request():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers["Authorization"])
        return httpx.Response(200)

    class Counter:
        n = 0

        def bearer(self) -> str:
            self.n += 1
            return f"t{self.n}"

    with httpx.Client(transport=httpx.MockTransport(handler), auth=EvalkitAuth(Counter())) as http:
        http.get("https://x/a")
        http.get("https://x/b")
    assert calls == ["Bearer t1", "Bearer t2"]


def providers_transport(idp: FakeIdp) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/providers":
            return httpx.Response(
                200,
                json={
                    "password": "admin",
                    "oidc": {
                        "token_endpoint": f"{idp.issuer}/token",
                        "authorize_params": {"resource": idp.audience},
                    },
                },
            )
        return idp._handle_httpx(request)

    return httpx.MockTransport(handler)


def test_build_auth_picks_the_source(tmp_path):
    idp = FakeIdp()
    idp.machine_clients["ci"] = ("s3cret", "machine-sub")
    assert build_auth(Connection("https://x", None, None)) is None
    assert build_auth(Connection("https://x", "tok", None)).source.bearer() == "tok"
    conn, path = stored(idp, tmp_path, seconds_left=3600)
    assert (
        build_auth(conn, path=path, transport=idp.transport()).source.bearer()
        == conn.oidc.access_token
    )
    machine = Connection(
        "https://evalkit.test", None, None, from_env=True, machine=MachineLogin("ci", "s3cret")
    )
    auth = build_auth(machine, transport=providers_transport(idp))
    assert jwt.decode(auth.source.bearer(), options={"verify_signature": False})["sub"] == (
        "machine-sub"
    )


def test_static_auth():
    assert static_auth("t").source.bearer() == "t"


def test_build_auth_without_an_identity_provider_names_it():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"password": "admin", "oidc": None})
    )
    machine = Connection(
        "https://evalkit.test", None, None, from_env=True, machine=MachineLogin("ci", "s3cret")
    )
    with pytest.raises(LookupError, match="identity provider"):
        build_auth(machine, transport=transport)
