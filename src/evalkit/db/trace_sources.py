from datetime import UTC, datetime

from pydantic import BaseModel, SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import TraceProvider
from evalkit.db import models
from evalkit.db.crypto import SecretBox
from evalkit.db.locks import lock_name


class UnknownTraceSource(KeyError):
    def __init__(self, name: str):
        super().__init__(f"unknown trace source: {name}")


class TraceSourceExists(ValueError):
    def __init__(self, name: str):
        super().__init__(f"trace source exists: {name}")


class EncryptionKeyMissing(RuntimeError):
    def __init__(self) -> None:
        super().__init__("encryption_key is not configured")


class TraceSourceView(BaseModel):
    id: int
    name: str
    provider: TraceProvider
    url: str
    archived: bool


class ResolvedTraceSource(BaseModel):
    id: int
    name: str
    provider: TraceProvider
    url: str
    api_key: SecretStr | None


def _view(row: models.TraceSource) -> TraceSourceView:
    return TraceSourceView(
        id=row.id,
        name=row.name,
        provider=TraceProvider(row.provider),
        url=row.config["url"],
        archived=row.archived_at is not None,
    )


async def count_trace_sources(session_factory: async_sessionmaker[AsyncSession]) -> int:
    async with session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(models.TraceSource)) or 0)


class TraceSourceStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], box: SecretBox | None):
        self._sessions = session_factory
        self._box = box

    def _seal(self, api_key: str) -> bytes:
        if self._box is None:
            raise EncryptionKeyMissing()
        return self._box.seal(api_key)

    async def _row(self, session: AsyncSession, name: str) -> models.TraceSource:
        row = await session.scalar(
            select(models.TraceSource).where(models.TraceSource.name == name)
        )
        if row is None:
            raise UnknownTraceSource(name)
        return row

    async def create(
        self, name: str, provider: TraceProvider, url: str, api_key: str | None
    ) -> TraceSourceView:
        sealed = self._seal(api_key) if api_key else None
        async with self._sessions() as session:
            await lock_name(session, "trace-source", name)
            if await session.scalar(
                select(models.TraceSource.id).where(models.TraceSource.name == name)
            ):
                raise TraceSourceExists(name)
            row = models.TraceSource(
                name=name, provider=provider.value, config={"url": url}, api_key_encrypted=sealed
            )
            session.add(row)
            await session.commit()
            return _view(row)

    async def edit(
        self, name: str, *, url: str | None = None, api_key: str | None = None
    ) -> TraceSourceView:
        async with self._sessions() as session:
            row = await self._row(session, name)
            if url is not None:
                row.config = {**row.config, "url": url}
            if api_key is not None:
                row.api_key_encrypted = self._seal(api_key)
            await session.commit()
            return _view(row)

    async def archive(self, name: str) -> TraceSourceView:
        async with self._sessions() as session:
            row = await self._row(session, name)
            row.archived_at = datetime.now(UTC)
            await session.commit()
            return _view(row)

    async def list_sources(self, include_archived: bool = False) -> list[TraceSourceView]:
        query = select(models.TraceSource).order_by(models.TraceSource.name)
        if not include_archived:
            query = query.where(models.TraceSource.archived_at.is_(None))
        async with self._sessions() as session:
            return [_view(row) for row in (await session.scalars(query)).all()]

    async def resolve(self, name: str) -> ResolvedTraceSource:
        async with self._sessions() as session:
            row = await self._row(session, name)
        if row.archived_at is not None:
            raise UnknownTraceSource(name)
        api_key = None
        if row.api_key_encrypted is not None:
            if self._box is None:
                raise EncryptionKeyMissing()
            api_key = self._box.open(row.api_key_encrypted)
        return ResolvedTraceSource(
            id=row.id,
            name=row.name,
            provider=TraceProvider(row.provider),
            url=row.config["url"],
            api_key=SecretStr(api_key) if api_key is not None else None,
        )
