from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import MappingStatus
from evalkit.db import models


class MappingRecord(BaseModel):
    id: int
    version: int
    status: str
    document: dict[str, Any]
    created_by: str | None
    created_at: datetime
    activated_at: datetime | None


def _to_record(row: models.SpanMapping, author: str | None) -> MappingRecord:
    return MappingRecord(
        id=row.id,
        version=row.version,
        status=row.status,
        document=row.mapping,
        created_by=author,
        created_at=row.created_at,
        activated_at=row.activated_at,
    )


def _with_author(query: Select) -> Select:
    return query.add_columns(models.User.username).outerjoin(
        models.User, models.User.id == models.SpanMapping.created_by_id
    )


async def active_mapping(session: AsyncSession, agent_id: int) -> models.SpanMapping | None:
    return await session.scalar(
        select(models.SpanMapping).where(
            models.SpanMapping.agent_id == agent_id,
            models.SpanMapping.status == MappingStatus.ACTIVE.value,
        )
    )


async def retire_active(session: AsyncSession, agent_id: int, keep: int | None = None) -> None:
    current = await active_mapping(session, agent_id)
    if current is not None and current.id != keep:
        current.status = MappingStatus.RETIRED.value
        await session.flush()


async def next_version(session: AsyncSession, agent_id: int) -> int:
    last = await session.scalar(
        select(func.max(models.SpanMapping.version)).where(models.SpanMapping.agent_id == agent_id)
    )
    return (last or 0) + 1


async def activate_document(
    session: AsyncSession, agent_id: int, document: dict[str, Any], created_by_id: int | None
) -> models.SpanMapping:
    await retire_active(session, agent_id)
    row = models.SpanMapping(
        agent_id=agent_id,
        version=await next_version(session, agent_id),
        status=MappingStatus.ACTIVE.value,
        mapping=document,
        created_by_id=created_by_id,
        activated_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    return row


class MappingStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sessions = session_factory

    async def list(self, agent_id: int) -> list[MappingRecord]:
        async with self._sessions() as session:
            rows = (
                await session.execute(
                    _with_author(
                        select(models.SpanMapping)
                        .where(models.SpanMapping.agent_id == agent_id)
                        .order_by(models.SpanMapping.version)
                    )
                )
            ).all()
        return [_to_record(row, author) for row, author in rows]

    async def _with_status(self, agent_id: int, status: str) -> MappingRecord | None:
        async with self._sessions() as session:
            found = (
                await session.execute(
                    _with_author(
                        select(models.SpanMapping).where(
                            models.SpanMapping.agent_id == agent_id,
                            models.SpanMapping.status == status,
                        )
                    )
                )
            ).one_or_none()
        return _to_record(found[0], found[1]) if found is not None else None

    async def active(self, agent_id: int) -> MappingRecord | None:
        return await self._with_status(agent_id, "active")

    async def draft(self, agent_id: int) -> MappingRecord | None:
        return await self._with_status(agent_id, "draft")

    async def put_draft(
        self, agent_id: int, document: dict[str, Any], created_by: str
    ) -> MappingRecord:
        async with self._sessions() as session:
            current = (
                await session.execute(
                    select(models.SpanMapping).where(
                        models.SpanMapping.agent_id == agent_id,
                        models.SpanMapping.status == "draft",
                    )
                )
            ).scalar_one_or_none()
            if current is not None:
                current.status = "retired"
                await session.flush()
            row = models.SpanMapping(
                agent_id=agent_id,
                version=await next_version(session, agent_id),
                status="draft",
                mapping=document,
                created_by_id=await session.scalar(
                    select(models.User.id).where(models.User.username == created_by)
                ),
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _to_record(row, created_by)

    async def activate(self, agent_id: int, version: int) -> MappingRecord:
        async with self._sessions() as session:
            row = (
                await session.execute(
                    select(models.SpanMapping).where(
                        models.SpanMapping.agent_id == agent_id,
                        models.SpanMapping.version == version,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                raise KeyError(f"no mapping version {version}")
            await retire_active(session, agent_id, keep=row.id)
            row.status = "active"
            row.activated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            author = await session.scalar(
                select(models.User.username).where(models.User.id == row.created_by_id)
            )
            return _to_record(row, author)
