import httpx
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
    state = OidcState(cfg_for(idp), httpx.AsyncClient(transport=idp.transport()), RESOURCE)
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

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down")
        return idp._handle_httpx(request)

    state = OidcState(
        cfg_for(idp), httpx.AsyncClient(transport=httpx.MockTransport(flaky)), RESOURCE
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
