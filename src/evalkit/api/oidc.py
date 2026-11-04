from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx
import jwt
from fastapi import HTTPException

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


@dataclass(frozen=True)
class Discovery:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    device_authorization_endpoint: str | None


class JwkCache:
    def __init__(self, jwks_uri: str, http: httpx.AsyncClient) -> None:
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
        async with self.lock:
            if kid not in self.keys and time.monotonic() - self.last_fetch >= JWKS_REFETCH_SECONDS:
                self.last_fetch = time.monotonic()
                try:
                    await self.fetch()
                except httpx.HTTPError as exc:
                    raise IdpUnavailable() from exc
        if kid not in self.keys:
            raise HTTPException(
                status_code=401, detail=f"invalid access token: unknown signing key {kid}"
            )
        return self.keys[kid]


class OidcState:
    def __init__(self, cfg: OidcConfig, http: httpx.AsyncClient, mcp_resource_url: str) -> None:
        self.cfg = cfg
        self.http = http
        self.mcp_resource_url = mcp_resource_url
        self.discovery: Discovery | None = None
        self.keys: JwkCache | None = None

    async def load(self) -> bool:
        try:
            resp = await self.http.get(f"{self.cfg.issuer}{DISCOVERY_PATH}")
            resp.raise_for_status()
            doc = resp.json()
            keys = JwkCache(doc["jwks_uri"], self.http)
            await keys.fetch()
        except httpx.HTTPError as exc:
            LOGGER.warning("identity provider %s unreachable: %s", self.cfg.issuer, exc)
            return False
        self.discovery = Discovery(
            authorization_endpoint=doc["authorization_endpoint"],
            token_endpoint=doc["token_endpoint"],
            jwks_uri=doc["jwks_uri"],
            device_authorization_endpoint=doc.get("device_authorization_endpoint"),
        )
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
