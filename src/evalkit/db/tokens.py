from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import cast

from pydantic import BaseModel
from sqlalchemy import CursorResult, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import TokenKind
from evalkit.core.workspace import Reference, slug
from evalkit.db import models


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AgentTokenRecord(BaseModel):
    id: int
    agent: str
    created_by: str
    created_at: datetime
    last_used_at: datetime | None


class AgentTokens:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sessions = session_factory

    async def create(self, *, agent_id: int, user_id: int) -> tuple[str, AgentTokenRecord]:
        token = secrets.token_urlsafe(32)
        async with self._sessions() as session:
            row = models.AuthToken(
                user_id=user_id,
                token_hash=token_hash(token),
                expires_at=None,
                kind=TokenKind.AGENT,
                agent_id=agent_id,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            records = await self._records(session, [row])
        return token, records[0]

    async def _records(
        self, session: AsyncSession, rows: list[models.AuthToken]
    ) -> list[AgentTokenRecord]:
        out: list[AgentTokenRecord] = []
        for row in rows:
            name, workspace = (
                await session.execute(
                    select(models.Agent.name, models.Workspace.name)
                    .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
                    .where(models.Agent.id == row.agent_id)
                )
            ).one()
            user = (
                await session.execute(select(models.User).where(models.User.id == row.user_id))
            ).scalar_one()
            out.append(
                AgentTokenRecord(
                    id=row.id,
                    agent=slug(Reference(name, workspace)),
                    created_by=user.username,
                    created_at=row.created_at,
                    last_used_at=row.last_used_at,
                )
            )
        return out

    async def list(self, *, user_id: int | None) -> list[AgentTokenRecord]:
        where = [models.AuthToken.kind == TokenKind.AGENT]
        if user_id is not None:
            where.append(models.AuthToken.user_id == user_id)
        async with self._sessions() as session:
            rows = (
                (
                    await session.execute(
                        select(models.AuthToken).where(*where).order_by(models.AuthToken.id)
                    )
                )
                .scalars()
                .all()
            )
            return await self._records(session, list(rows))

    async def revoke(self, token_id: int, *, user_id: int | None) -> bool:
        where = [models.AuthToken.id == token_id, models.AuthToken.kind == TokenKind.AGENT]
        if user_id is not None:
            where.append(models.AuthToken.user_id == user_id)
        async with self._sessions() as session:
            result = cast(
                CursorResult, await session.execute(delete(models.AuthToken).where(*where))
            )
            await session.commit()
        return bool(result.rowcount)
