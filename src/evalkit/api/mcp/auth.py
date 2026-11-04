from fastapi import HTTPException
from fastmcp.server.auth import AccessToken, AuthProvider, TokenVerifier
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
        try:
            row, user = await bearer_login(self._session_factory, token)
        except HTTPException:
            return None
        expires = token_expiry(row)
        return AccessToken(
            token=token,
            client_id=f"evalkit:{user.username}",
            scopes=[SCOPE_READ],
            expires_at=int(expires.timestamp()) if expires is not None else None,
            claims={
                "sub": user.username,
                "evalkit_user": user.username,
                "tenant_id": user.tenant_id,
            },
        )


def build_auth(cfg: Config, session_factory: async_sessionmaker[AsyncSession]) -> AuthProvider:
    return EvalkitTokenVerifier(session_factory, cfg.server.base_url, required_scopes=[SCOPE_READ])
