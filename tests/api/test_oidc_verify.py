import asyncio

import httpx2
import pytest
from fastapi import HTTPException

from evalkit.api.oidc import OidcState, verify_access_token
from evalkit.config import OidcConfig
from tests.identity import FakeIdp

RESOURCE = "https://evalkit.test/mcp"


def cfg_for(idp: FakeIdp) -> OidcConfig:
    return OidcConfig(
        issuer=idp.issuer, client_id=idp.client_id, audience=idp.audience, name="Fake"
    )


@pytest.fixture
async def oidc():
    idp = FakeIdp()
    state = OidcState(cfg_for(idp), httpx2.AsyncClient(transport=idp.transport()), RESOURCE)
    assert await state.load() is True
    yield idp, state
    await state.http.aclose()


async def test_valid_token_yields_claims(oidc):
    idp, state = oidc
    claims = await verify_access_token(state, idp.access_token("sub-1"), [idp.audience])
    assert claims["sub"] == "sub-1" and claims["iss"] == idp.issuer


@pytest.mark.parametrize(
    "token_kwargs,reason",
    [
        ({"issuer": "https://other.test"}, "issuer"),
        ({"audience": "https://other.test"}, "audience"),
        ({"expires_in": -120}, "expired"),
    ],
)
async def test_bad_claims_are_401_with_the_reason(oidc, token_kwargs, reason):
    idp, state = oidc
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, idp.access_token("sub-1", **token_kwargs), [idp.audience])
    assert info.value.status_code == 401 and reason in info.value.detail.lower()


async def test_only_the_given_audiences_are_accepted(oidc):
    idp, state = oidc
    token = idp.access_token("sub-1", audience=RESOURCE)
    with pytest.raises(HTTPException):
        await verify_access_token(state, token, [idp.audience])
    assert (await verify_access_token(state, token, [idp.audience, RESOURCE]))["sub"] == "sub-1"


async def test_bad_signature_is_401(oidc):
    idp, state = oidc
    other = FakeIdp()
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, other.access_token("sub-1"), [idp.audience])
    assert info.value.status_code == 401


async def test_unknown_kid_refetches_once_then_fails(oidc):
    idp, state = oidc
    before = idp.jwks_fetches
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, idp.access_token("sub-1", kid="k2"), [idp.audience])
    assert info.value.status_code == 401 and "signing key" in info.value.detail
    assert idp.jwks_fetches == before + 1
    with pytest.raises(HTTPException):
        await verify_access_token(state, idp.access_token("sub-1", kid="k2"), [idp.audience])
    assert idp.jwks_fetches == before + 1


async def test_rotated_key_is_picked_up_after_a_refetch(oidc):
    idp, state = oidc
    idp.kid = "k2"
    state.keys.last_fetch = 0.0
    claims = await verify_access_token(state, idp.access_token("sub-1"), [idp.audience])
    assert claims["sub"] == "sub-1"


async def test_discovery_down_means_503_until_it_comes_back():
    idp = FakeIdp()
    calls = {"n": 0}

    def flaky(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx2.ConnectError("down")
        return idp._handle_httpx(request)

    state = OidcState(
        cfg_for(idp), httpx2.AsyncClient(transport=httpx2.MockTransport(flaky)), RESOURCE
    )
    assert await state.load() is False
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, idp.access_token("sub-1"), [idp.audience])
    assert info.value.status_code == 503
    assert await state.load() is True
    assert (await verify_access_token(state, idp.access_token("sub-1"), [idp.audience]))[
        "sub"
    ] == "sub-1"
    await state.http.aclose()


async def test_a_token_that_is_not_a_jwt_is_401(oidc):
    idp, state = oidc
    with pytest.raises(HTTPException) as info:
        await verify_access_token(state, "not.a.jwt", [idp.audience])
    assert info.value.status_code == 401


async def test_failed_jwks_refetch_is_503_and_keeps_the_throttle():
    idp = FakeIdp()
    attempts = {"jwks": 0}
    down = {"on": False}

    def flaky(request: httpx2.Request) -> httpx2.Response:
        if down["on"] and request.url.path.endswith("/jwks"):
            attempts["jwks"] += 1
            raise httpx2.ConnectError("down")
        return idp._handle_httpx(request)

    state = OidcState(
        cfg_for(idp), httpx2.AsyncClient(transport=httpx2.MockTransport(flaky)), RESOURCE
    )
    assert await state.load() is True
    down["on"] = True
    token = idp.access_token("sub-1", kid="k2")
    with pytest.raises(HTTPException) as first:
        await verify_access_token(state, token, [idp.audience])
    assert first.value.status_code == 503
    with pytest.raises(HTTPException) as second:
        await verify_access_token(state, token, [idp.audience])
    assert second.value.status_code == 401 and "signing key" in second.value.detail
    assert attempts["jwks"] == 1
    await state.http.aclose()


@pytest.mark.parametrize(
    "discovery_body",
    [
        b"<html>maintenance</html>",
        b"{}",
        b'{"authorization_endpoint": "a", "token_endpoint": "t"}',
        b'{"authorization_endpoint": "a", "jwks_uri": "j"}',
    ],
)
async def test_a_bad_discovery_body_is_an_unreachable_idp(discovery_body):
    idp = FakeIdp()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=discovery_body)

    state = OidcState(
        cfg_for(idp), httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), RESOURCE
    )
    assert await state.load() is False
    assert state.discovery is None and state.keys is None
    await state.http.aclose()


@pytest.mark.parametrize("jwks_body", [b"<html>maintenance</html>", b"{}", b'{"keys": []}'])
async def test_a_bad_jwks_body_is_an_unreachable_idp(jwks_body):
    idp = FakeIdp()

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/jwks":
            return httpx2.Response(200, content=jwks_body)
        return httpx2.Response(200, json=idp.discovery())

    state = OidcState(
        cfg_for(idp), httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), RESOURCE
    )
    assert await state.load() is False
    assert state.keys is None
    await state.http.aclose()


async def test_a_known_kid_does_not_wait_on_an_inflight_refetch(oidc):
    idp, state = oidc
    keys = state.keys
    await keys.lock.acquire()
    try:
        key = await asyncio.wait_for(keys.signing_key(idp.access_token("sub-1")), timeout=1)
    finally:
        keys.lock.release()
    assert key.key_id == idp.kid
