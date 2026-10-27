from fastapi import HTTPException
from fastmcp.server.auth import (
    AccessToken,
    AuthProvider,
    MultiAuth,
    RemoteAuthProvider,
    TokenVerifier,
)
from fastmcp.server.auth.providers.jwt import JWTVerifier
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.api.auth import bearer_login, token_expiry
from evalkit.config import Config

SCOPE_READ = "evalkit:read"
MCP_PATH = "/mcp"


def resource_url(cfg: Config) -> str:
    return f"{cfg.server.base_url.rstrip('/')}{MCP_PATH}"


# An evalkit login token reaches MCP with the read scope; agent tokens stay
# collector-only here as on /api.
class EvalkitTokenVerifier(TokenVerifier):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        base_url: str,
        required_scopes: list[str] | None = None,
    ):
        super().__init__(base_url=base_url, required_scopes=required_scopes)
        self._session_factory = session_factory

    async def verify_token(self, token: str) -> AccessToken | None:
        async with self._session_factory() as session:
            try:
                row, user = await bearer_login(session, token)
            except HTTPException:
                return None
            await session.commit()
        expires = token_expiry(row)
        return AccessToken(
            token=token,
            client_id=f"evalkit:{user.username}",
            scopes=[SCOPE_READ],
            expires_at=int(expires.timestamp()) if expires is not None else None,
            claims={"sub": user.username, "evalkit_user": user.username},
        )


# The identity provider names the scope (`mcp.oauth.scope`); a token carrying it
# gets evalkit's own read scope, which the tools check.
class ProviderScopeVerifier(TokenVerifier):
    def __init__(self, inner: TokenVerifier, scope: str, base_url: str):
        super().__init__(base_url=base_url)
        self._inner = inner
        self._scope = scope

    async def verify_token(self, token: str) -> AccessToken | None:
        found = await self._inner.verify_token(token)
        if found is None:
            return None
        scopes = [s for s in found.scopes if s != SCOPE_READ]
        if self._scope in found.scopes:
            scopes.append(SCOPE_READ)
        return found.model_copy(update={"scopes": scopes})


def build_auth(
    cfg: Config,
    session_factory: async_sessionmaker[AsyncSession],
    jwt_verifier: TokenVerifier | None = None,
) -> AuthProvider:
    oauth = cfg.mcp.oauth
    if oauth is None:
        return EvalkitTokenVerifier(
            session_factory, cfg.server.base_url, required_scopes=[SCOPE_READ]
        )
    verifier = jwt_verifier or JWTVerifier(
        jwks_uri=oauth.jwks_uri,
        issuer=oauth.issuer,
        audience=oauth.audience or resource_url(cfg),
        algorithm=oauth.algorithm,
    )
    remote = RemoteAuthProvider(
        token_verifier=ProviderScopeVerifier(verifier, oauth.scope, cfg.server.base_url),
        # As configured: AnyHttpUrl would turn https://host into https://host/, which clients
        # cannot match to the provider's issuer (RFC 8414).
        authorization_servers=[oauth.issuer],  # type: ignore[list-item]
        base_url=cfg.server.base_url,
        scopes_supported=[oauth.scope],
        resource_name="evalkit",
    )
    return MultiAuth(
        server=remote,
        verifiers={"evalkit": EvalkitTokenVerifier(session_factory, cfg.server.base_url)},
        server_source_id="oauth",
        required_scopes=[SCOPE_READ],
    )
