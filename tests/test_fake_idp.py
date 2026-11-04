import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pytest

from tests.identity import FakeIdp


@pytest.fixture
def idp() -> FakeIdp:
    return FakeIdp()


def test_discovery_and_jwks(idp):
    with httpx.Client(transport=idp.transport()) as http:
        disc = http.get(f"{idp.issuer}/.well-known/openid-configuration").json()
        assert disc["issuer"] == idp.issuer
        assert disc["token_endpoint"] == f"{idp.issuer}/token"
        keys = http.get(disc["jwks_uri"]).json()["keys"]
    assert keys[0]["kid"] == idp.kid
    token = idp.access_token("sub-1")
    key = jwt.PyJWKSet.from_dict({"keys": keys})[idp.kid]
    claims = jwt.decode(
        token, key.key, algorithms=["RS256"], audience=idp.audience, issuer=idp.issuer
    )
    assert claims["sub"] == "sub-1"


def test_code_flow_with_pkce_and_refresh_rotation(idp):
    idp.next_login = ("sub-1", "ann@example.com", "ann")
    verifier = "v" * 43
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )
    redirect_uri = "http://127.0.0.1:1/callback"
    query = urlencode(
        {
            "client_id": idp.client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "scope": "openid email profile offline_access",
            "state": "st-1",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    token_url = f"{idp.issuer}/token"
    with httpx.Client(transport=idp.transport()) as http:

        def exchange(code: str, code_verifier: str) -> httpx.Response:
            return http.post(
                token_url,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": idp.client_id,
                    "code_verifier": code_verifier,
                },
            )

        def authorize() -> str:
            resp = http.get(f"{idp.issuer}/authorize?{query}")
            assert resp.status_code == 302
            sent = parse_qs(urlsplit(resp.headers["location"]).query)
            assert sent["state"] == ["st-1"]
            return sent["code"][0]

        no_pkce = http.get(
            f"{idp.issuer}/authorize?{urlencode({'redirect_uri': redirect_uri, 'state': 'st-1'})}"
        )
        assert no_pkce.status_code == 400 and no_pkce.json()["error"] == "invalid_request"

        bad = exchange(authorize(), "w" * 43)
        assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"

        resp = exchange(authorize(), verifier)
        assert resp.status_code == 200
        tokens = resp.json()
        assert set(tokens) >= {"access_token", "id_token", "refresh_token", "expires_in"}
        idtok = jwt.decode(tokens["id_token"], options={"verify_signature": False})
        assert idtok["email"] == "ann@example.com" and idtok["preferred_username"] == "ann"
        first = tokens["refresh_token"]

        def refresh(token: str) -> httpx.Response:
            return http.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": token,
                    "client_id": idp.client_id,
                },
            )

        rotated = refresh(first)
        assert rotated.status_code == 200
        assert rotated.json()["refresh_token"] != first
        reused = refresh(first)
        assert reused.status_code == 400 and reused.json()["error"] == "invalid_grant"


def test_client_credentials_and_device_grant(idp):
    idp.machine_clients["ci"] = ("s3cret", "machine-sub")
    token_url = f"{idp.issuer}/token"
    with httpx.Client(transport=idp.transport()) as http:
        resp = http.post(
            token_url,
            data={"grant_type": "client_credentials"},
            auth=httpx.BasicAuth("ci", "s3cret"),
        )
        assert resp.status_code == 200
        claims = jwt.decode(resp.json()["access_token"], options={"verify_signature": False})
        assert claims["sub"] == "machine-sub"

        idp.next_login = ("sub-2", "bob@example.com", None)
        idp.device_pending = 1
        started = http.post(
            f"{idp.issuer}/device", data={"client_id": idp.client_id, "scope": "openid"}
        ).json()
        assert {"device_code", "user_code", "verification_uri", "interval"} <= set(started)
        poll = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": started["device_code"],
            "client_id": idp.client_id,
        }
        pending = http.post(token_url, data=poll)
        assert pending.status_code == 400 and pending.json()["error"] == "authorization_pending"
        done = http.post(token_url, data=poll)
        assert done.status_code == 200
        assert (
            jwt.decode(done.json()["id_token"], options={"verify_signature": False})["sub"]
            == "sub-2"
        )
        again = http.post(token_url, data=poll)
        assert again.status_code == 400 and again.json()["error"] == "expired_token"


async def test_asgi_adapter_serves_discovery(idp):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=idp.asgi()), base_url=idp.issuer
    ) as http:
        disc = (await http.get("/.well-known/openid-configuration")).json()
    assert disc["jwks_uri"] == f"{idp.issuer}/jwks"
