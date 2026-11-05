from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.db.models import Agent, AuthToken, User, Workspace
from evalkit.db.session import ALL_TENANTS, tenant_session


async def find_token(
    session_factory: async_sessionmaker[AsyncSession], token_hash: str
) -> tuple[AuthToken, str] | None:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        found = (
            await session.execute(
                select(AuthToken, func.coalesce(Workspace.tenant_id, User.tenant_id))
                .join(User, User.id == AuthToken.user_id)
                .outerjoin(Agent, Agent.id == AuthToken.agent_id)
                .outerjoin(Workspace, Workspace.id == Agent.workspace_id)
                .where(AuthToken.token_hash == token_hash)
            )
        ).one_or_none()
    if found is None:
        return None
    return found[0], str(found[1])
