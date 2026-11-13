from __future__ import annotations

import asyncio
import logging
import time

import httpx2
import jwt
from fastapi import HTTPException
from pydantic import BaseModel

from evalkit.config import OidcConfig

LOGGER = logging.getLogger(__name__)
DISCOVERY_PATH = "/.well-known/openid-configuration"
JWKS_REFETCH_SECONDS = 60.0
DISCOVERY_RETRY_SECONDS = 30.0
LEEWAY_SECONDS = 30
REQUIRED_CLAIMS = ["exp", "iat", "sub"]


class IdpUnavailable(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=503, detail="identity provider unreachable")


class Discovery(BaseModel, frozen=True):
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    device_authorization_endpoint: str | None = None


class JwkCache:
    def __init__(self, jwks_uri: str, http: httpx2.AsyncClient) -> None:
        self.jwks_uri = jwks_uri
        self.http = http
        self.keys: dict[str, jwt.PyJWK] = {}
        self.last_fetch = 0.0
        self.lock = asyncio.Lock()

    async def fetch(self) -> None:
        resp = await self.http.get(self.jwks_uri)
        resp.raise_for_status()
        self.keys = {
            key.key_id: key for key in jwt.PyJWKSet.from_dict(resp.json()).keys if key.key_id
        }

    async def signing_key(self, token: str) -> jwt.PyJWK:
        kid = jwt.get_unverified_header(token).get("kid")
        if kid is None:
            raise HTTPException(status_code=401, detail="invalid access token: no kid in header")
        if kid in self.keys:
            return self.keys[kid]
        async with self.lock:
            if kid not in self.keys and time.monotonic() - self.last_fetch >= JWKS_REFETCH_SECONDS:
                self.last_fetch = time.monotonic()
                try:
                    await self.fetch()
                except (httpx2.HTTPError, jwt.PyJWTError, ValueError) as exc:
                    raise IdpUnavailable() from exc
        if kid not in self.keys:
            raise HTTPException(
                status_code=401, detail=f"invalid access token: unknown signing key {kid}"
            )
        return self.keys[kid]


class OidcState:
    def __init__(self, cfg: OidcConfig, http: httpx2.AsyncClient, mcp_resource_url: str) -> None:
        self.cfg = cfg
        self.http = http
        self.mcp_resource_url = mcp_resource_url
        self.discovery: Discovery | None = None
        self.keys: JwkCache | None = None

    async def load(self) -> bool:
        try:
            resp = await self.http.get(f"{self.cfg.issuer}{DISCOVERY_PATH}")
            resp.raise_for_status()
            discovery = Discovery.model_validate_json(resp.content)
            keys = JwkCache(discovery.jwks_uri, self.http)
            await keys.fetch()
        except (httpx2.HTTPError, jwt.PyJWTError, ValueError) as exc:
            LOGGER.warning("identity provider %s unusable: %s", self.cfg.issuer, exc)
            return False
        self.discovery = discovery
        self.keys = keys
        return True

    async def retry_forever(self, interval: float = DISCOVERY_RETRY_SECONDS) -> None:
        while self.discovery is None:
            await asyncio.sleep(interval)
            await self.load()


async def verify_access_token(oidc: OidcState, token: str, audience: list[str]) -> dict:
    if oidc.keys is None:
        raise IdpUnavailable()
    try:
        key = await oidc.keys.signing_key(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=oidc.cfg.algorithms,
            issuer=oidc.cfg.issuer,
            audience=audience,
            options={"require": REQUIRED_CLAIMS},
            leeway=LEEWAY_SECONDS,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid access token: {exc}") from exc


def token_scopes(claims: dict) -> set[str]:
    scopes: set[str] = set()
    for claim in ("scope", "scp"):
        value = claims.get(claim)
        if isinstance(value, str):
            scopes.update(value.split())
        elif isinstance(value, list):
            scopes.update(item for item in value if isinstance(item, str))
    return scopes


def require_scopes(oidc: OidcState, claims: dict) -> None:
    missing = [scope for scope in oidc.cfg.required_scopes if scope not in token_scopes(claims)]
    if missing:
        raise HTTPException(
            status_code=403,
            detail=f"access token lacks required scope {' '.join(missing)}",
        )


async def verify_id_token(oidc: OidcState, token: str) -> dict:
    if oidc.keys is None:
        raise IdpUnavailable()
    try:
        key = await oidc.keys.signing_key(token)
        return jwt.decode(
            token,
            key.key,
            algorithms=oidc.cfg.algorithms,
            issuer=oidc.cfg.issuer,
            audience=[oidc.cfg.client_id, oidc.cfg.resolve_cli_client_id()],
            options={"require": REQUIRED_CLAIMS},
            leeway=LEEWAY_SECONDS,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid id_token: {exc}") from exc
