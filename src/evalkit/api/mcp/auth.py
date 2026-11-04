from fastapi import HTTPException
from fastmcp.server.auth import AccessToken, AuthProvider, RemoteAuthProvider, TokenVerifier

from evalkit.api.auth import bearer_login, is_jwt, sso_user, token_expiry

SCOPE_READ = "evalkit:read"
MCP_PATH = "/mcp"


# An evalkit login token reaches MCP with the read scope; agent tokens stay
# collector-only here as on /api.
class EvalkitTokenVerifier(TokenVerifier):
    def __init__(self, state, base_url: str, required_scopes: list[str] | None = None):
        super().__init__(base_url=base_url, required_scopes=required_scopes)
        self._state = state

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            if is_jwt(token):
                oidc = self._state.oidc
                audience = [oidc.cfg.audience, oidc.mcp_resource_url] if oidc is not None else []
                user = await sso_user(self._state, token, audience, None)
                expires = None
            else:
                row, user = await bearer_login(self._state.session_factory, token)
                expires = token_expiry(row)
        except HTTPException:
            return None
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


def build_auth(state) -> AuthProvider:
    base_url = state.cfg.server.base_url
    verifier = EvalkitTokenVerifier(state, base_url, required_scopes=[SCOPE_READ])
    if state.oidc is None:
        return verifier
    return RemoteAuthProvider(
        token_verifier=verifier,
        # As configured: AnyHttpUrl would turn https://host into https://host/, which clients
        # cannot match to the provider's issuer (RFC 8414).
        authorization_servers=[state.oidc.cfg.issuer],  # type: ignore[list-item]
        base_url=base_url,
        scopes_supported=state.oidc.cfg.scopes,
        resource_name="evalkit",
    )
