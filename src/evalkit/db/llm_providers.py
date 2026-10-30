from datetime import UTC, datetime

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.settings import JudgeSettings
from evalkit.db import models
from evalkit.db.locks import lock_name


class UnknownLlmProvider(KeyError):
    def __init__(self, name: str):
        super().__init__(
            f"unknown LLM provider: {name} — create it with evalkit llm-providers create"
        )


class LlmProviderExists(ValueError):
    pass


class ProviderInUse(ValueError):
    pass


class LlmProviderView(BaseModel):
    name: str
    base_url: str
    has_api_key: bool
    archived: bool


def _view(row: models.LlmProvider) -> LlmProviderView:
    return LlmProviderView(
        name=row.name,
        base_url=row.base_url,
        has_api_key=row.api_key_encrypted is not None,
        archived=row.archived_at is not None,
    )


class LlmProviderStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sessions = session_factory

    async def _row(self, session: AsyncSession, name: str) -> models.LlmProvider:
        row = await session.scalar(
            select(models.LlmProvider).where(models.LlmProvider.name == name)
        )
        if row is None:
            raise UnknownLlmProvider(name)
        return row

    async def _settings(self, session: AsyncSession) -> models.ServerSettings | None:
        return await session.get(models.ServerSettings, 1)

    async def _check_free(self, session: AsyncSession, name: str, base_url: str) -> None:
        taken = await session.scalar(
            select(models.LlmProvider.name).where(
                (models.LlmProvider.name == name) | (models.LlmProvider.base_url == base_url)
            )
        )
        if taken is not None:
            raise LlmProviderExists(f"LLM provider exists with that name or base URL: {taken}")

    async def create(
        self, name: str, base_url: str, api_key_encrypted: bytes | None
    ) -> LlmProviderView:
        async with self._sessions() as session:
            await lock_name(session, "llm-provider", name)
            await self._check_free(session, name, base_url)
            row = models.LlmProvider(
                name=name, base_url=base_url, api_key_encrypted=api_key_encrypted
            )
            session.add(row)
            await session.commit()
            return _view(row)

    async def edit(
        self,
        name: str,
        *,
        base_url: str | None = None,
        api_key_encrypted: bytes | None = None,
        clear_api_key: bool = False,
    ) -> LlmProviderView:
        async with self._sessions() as session:
            row = await self._row(session, name)
            if base_url is not None and base_url != row.base_url:
                settings = await self._settings(session)
                if (
                    settings is not None
                    and (settings.default_judge or {}).get("base_url") == row.base_url
                ):
                    raise ProviderInUse(
                        f"{name} is the default judge's provider; "
                        "set another default judge before changing its base URL"
                    )
                row.base_url = base_url
                row.api_key_encrypted = None
            if api_key_encrypted is not None or clear_api_key:
                row.api_key_encrypted = api_key_encrypted
            await session.commit()
            return _view(row)

    async def archive(self, name: str) -> LlmProviderView:
        async with self._sessions() as session:
            row = await self._row(session, name)
            settings = await self._settings(session)
            if (
                settings is not None
                and (settings.default_judge or {}).get("base_url") == row.base_url
            ):
                raise ProviderInUse(
                    f"{name} is the default judge's provider; set another default judge first"
                )
            row.archived_at = datetime.now(UTC)
            await session.commit()
            return _view(row)

    async def restore(self, name: str) -> LlmProviderView:
        async with self._sessions() as session:
            row = await self._row(session, name)
            row.archived_at = None
            await session.commit()
            return _view(row)

    async def get(self, name: str) -> LlmProviderView:
        async with self._sessions() as session:
            return _view(await self._row(session, name))

    async def list_providers(self, include_archived: bool = False) -> list[LlmProviderView]:
        query = select(models.LlmProvider).order_by(models.LlmProvider.name)
        if not include_archived:
            query = query.where(models.LlmProvider.archived_at.is_(None))
        async with self._sessions() as session:
            return [_view(row) for row in (await session.scalars(query)).all()]

    async def active_by_base_url(self, base_url: str) -> LlmProviderView | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(models.LlmProvider).where(
                    models.LlmProvider.base_url == base_url,
                    models.LlmProvider.archived_at.is_(None),
                )
            )
        return _view(row) if row is not None else None

    async def sealed_key(self, base_url: str) -> bytes | None:
        async with self._sessions() as session:
            return await session.scalar(
                select(models.LlmProvider.api_key_encrypted).where(
                    models.LlmProvider.base_url == base_url,
                    models.LlmProvider.archived_at.is_(None),
                )
            )

    async def default_judge(self) -> JudgeSettings | None:
        async with self._sessions() as session:
            settings = await self._settings(session)
        if settings is None or settings.default_judge is None:
            return None
        return JudgeSettings.model_validate(settings.default_judge)

    async def set_default_judge(self, judge: JudgeSettings) -> JudgeSettings:
        if await self.active_by_base_url(judge.base_url) is None:
            raise UnknownLlmProvider(judge.base_url)
        async with self._sessions() as session:
            settings = await self._settings(session)
            if settings is None:
                settings = models.ServerSettings(id=1)
                session.add(settings)
            settings.default_judge = judge.model_dump()
            await session.commit()
        return judge
