import threading
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest

from evalkit.client.oidc import (
    LoginRefused,
    LoginTimedOut,
    NoDeviceGrant,
    login_from_tokens,
    pkce_challenge,
    register_identity,
    run_device_flow,
    run_loopback_flow,
)
from tests.identity import FakeIdp


def providers_for(idp: FakeIdp, *, device: bool = True) -> dict:
    disc = idp.discovery()
    return {
        "name": "Fake",
        "issuer": idp.issuer,
        "client_id": idp.client_id,
        "audience": idp.audience,
        "scopes": ["openid", "profile", "email", "offline_access"],
        "authorize_params": {"resource": idp.audience},
        "authorization_endpoint": disc["authorization_endpoint"],
        "token_endpoint": disc["token_endpoint"],
        "device_authorization_endpoint": disc["device_authorization_endpoint"] if device else None,
    }


def browser_for(idp: FakeIdp):
    def open_browser(url: str) -> None:
        def follow() -> None:
            with httpx.Client(transport=idp.transport()) as at_idp:
                redirect = at_idp.get(url)
            assert redirect.status_code == 302, redirect.text
            httpx.get(redirect.headers["location"])

        threading.Thread(target=follow, daemon=True).start()

    return open_browser


def callback_for(url: str, **params: str) -> None:
    query = parse_qs(urlsplit(url).query)
    threading.Thread(
        target=lambda: httpx.get(query["redirect_uri"][0], params=params), daemon=True
    ).start()


def test_pkce_challenge_matches_the_rfc_example():
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_loopback_flow_sends_pkce_and_the_authorize_params():
    idp = FakeIdp()
    idp.next_login = ("sub-1", "ann@example.com", "ann")
    seen: list[str] = []

    def spy(url: str) -> None:
        seen.append(url)
        browser_for(idp)(url)

    tokens = run_loopback_flow(providers_for(idp), spy, transport=idp.transport())
    query = parse_qs(urlsplit(seen[0]).query)
    assert query["code_challenge_method"] == ["S256"] and query["resource"] == [idp.audience]
    assert query["redirect_uri"][0].startswith("http://127.0.0.1:")
    assert jwt.decode(tokens["id_token"], options={"verify_signature": False})["sub"] == "sub-1"
    assert "refresh_token" in tokens


def test_loopback_flow_times_out():
    idp = FakeIdp()
    with pytest.raises(LoginTimedOut):
        run_loopback_flow(
            providers_for(idp), lambda url: None, timeout=0.2, transport=idp.transport()
        )


def test_loopback_flow_refuses_a_callback_with_an_error():
    idp = FakeIdp()

    def spy(url: str) -> None:
        state = parse_qs(urlsplit(url).query)["state"][0]
        callback_for(url, error="access_denied", error_description="no", state=state)

    with pytest.raises(LoginRefused, match="access_denied"):
        run_loopback_flow(providers_for(idp), spy, transport=idp.transport())


def test_loopback_flow_refuses_a_state_mismatch():
    idp = FakeIdp()

    def spy(url: str) -> None:
        callback_for(url, code="c", state="forged")

    with pytest.raises(LoginRefused, match="state mismatch"):
        run_loopback_flow(providers_for(idp), spy, transport=idp.transport())


def test_device_flow_polls_until_the_person_finishes():
    idp = FakeIdp()
    idp.next_login = ("sub-2", "bob@example.com", None)
    idp.device_pending = 2
    lines: list[str] = []
    sleeps: list[float] = []
    tokens = run_device_flow(
        providers_for(idp), lines.append, sleep=sleeps.append, transport=idp.transport()
    )
    assert lines == [f"open {idp.issuer}/device/verify and enter ABCD-EFGH"]
    assert len(sleeps) == 2
    assert jwt.decode(tokens["id_token"], options={"verify_signature": False})["sub"] == "sub-2"


def test_device_flow_needs_the_endpoint():
    idp = FakeIdp()
    with pytest.raises(NoDeviceGrant):
        run_device_flow(providers_for(idp, device=False), print, transport=idp.transport())


def test_device_flow_refuses_any_other_error(monkeypatch):
    idp = FakeIdp()
    idp.next_login = ("sub-2", "bob@example.com", None)
    monkeypatch.setattr(
        idp,
        "_token",
        lambda form: (400, {"content-type": "application/json"}, b'{"error": "access_denied"}'),
    )
    with pytest.raises(LoginRefused, match="access_denied"):
        run_device_flow(providers_for(idp), print, sleep=lambda s: None, transport=idp.transport())


def test_register_identity_raises_the_servers_detail():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(409, json={"detail": "bind sub-1 first"})
    )
    with pytest.raises(LookupError, match="bind sub-1 first"):
        register_identity(
            "https://evalkit.test", {"access_token": "a", "id_token": "i"}, transport=transport
        )


def test_login_from_tokens_builds_the_connection():
    idp = FakeIdp()
    idp.next_login = ("sub-1", "ann@example.com", "ann")
    tokens = idp._issue(idp._login())
    conn = login_from_tokens("https://evalkit.test", providers_for(idp), tokens)
    assert conn.oidc is not None
    assert conn.oidc.refresh_token == tokens["refresh_token"]
    assert conn.oidc.token_endpoint == f"{idp.issuer}/token"
    assert conn.token is None
