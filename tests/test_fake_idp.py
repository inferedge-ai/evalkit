from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from authlib.integrations.httpx_client import OAuth2Client
from authlib.oauth2.rfc8628 import DEVICE_CODE_GRANT_TYPE

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
    client = OAuth2Client(
        idp.client_id,
        scope="openid email profile offline_access",
        redirect_uri="http://127.0.0.1:1/callback",
        code_challenge_method="S256",
        transport=idp.transport(),
    )
    url, state = client.create_authorization_url(f"{idp.issuer}/authorize", code_verifier="v" * 43)
    with httpx.Client(transport=idp.transport()) as http:
        resp = http.get(url)
    assert resp.status_code == 302
    sent = parse_qs(urlsplit(resp.headers["location"]).query)
    assert sent["state"] == [state]
    tokens = client.fetch_token(f"{idp.issuer}/token", code=sent["code"][0], code_verifier="v" * 43)
    assert set(tokens) >= {"access_token", "id_token", "refresh_token", "expires_in"}
    idtok = jwt.decode(tokens["id_token"], options={"verify_signature": False})
    assert idtok["email"] == "ann@example.com" and idtok["preferred_username"] == "ann"
    first = tokens["refresh_token"]
    rotated = client.refresh_token(f"{idp.issuer}/token", refresh_token=first)
    assert rotated["refresh_token"] != first
    with pytest.raises(Exception, match="invalid_grant"):
        client.refresh_token(f"{idp.issuer}/token", refresh_token=first)


def test_client_credentials_and_device_grant(idp):
    idp.machine_clients["ci"] = ("s3cret", "machine-sub")
    client = OAuth2Client("ci", "s3cret", transport=idp.transport())
    tokens = client.fetch_token(f"{idp.issuer}/token", grant_type="client_credentials")
    assert (
        jwt.decode(tokens["access_token"], options={"verify_signature": False})["sub"]
        == "machine-sub"
    )

    idp.next_login = ("sub-2", "bob@example.com", None)
    idp.device_pending = 1
    public = OAuth2Client(idp.client_id, transport=idp.transport())
    with httpx.Client(transport=idp.transport()) as http:
        started = http.post(
            f"{idp.issuer}/device", data={"client_id": idp.client_id, "scope": "openid"}
        ).json()
    assert {"device_code", "user_code", "verification_uri", "interval"} <= set(started)
    with pytest.raises(Exception, match="authorization_pending"):
        public.fetch_token(
            f"{idp.issuer}/token",
            grant_type=DEVICE_CODE_GRANT_TYPE,
            device_code=started["device_code"],
        )
    tokens = public.fetch_token(
        f"{idp.issuer}/token", grant_type=DEVICE_CODE_GRANT_TYPE, device_code=started["device_code"]
    )
    assert jwt.decode(tokens["id_token"], options={"verify_signature": False})["sub"] == "sub-2"


async def test_asgi_adapter_serves_discovery(idp):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=idp.asgi()), base_url=idp.issuer
    ) as http:
        disc = (await http.get("/.well-known/openid-configuration")).json()
    assert disc["jwks_uri"] == f"{idp.issuer}/jwks"
