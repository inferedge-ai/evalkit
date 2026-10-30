from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.db import models
from evalkit.db.crypto import SecretBox

type Sealed = models.TraceSource | models.Agent | models.RemoteEvaluator | models.LlmProvider

SEALED: tuple[type[Sealed], ...] = (
    models.TraceSource,
    models.Agent,
    models.RemoteEvaluator,
    models.LlmProvider,
)


async def count_sealed(session_factory: async_sessionmaker[AsyncSession]) -> int:
    count = 0
    async with session_factory() as session:
        for model in SEALED:
            count += int(
                await session.scalar(
                    select(func.count()).where(model.api_key_encrypted.is_not(None))
                )
                or 0
            )
    return count


async def _rekey[T: Sealed](
    session: AsyncSession, model: type[T], old: SecretBox, new: SecretBox
) -> int:
    rows = (await session.scalars(select(model).where(model.api_key_encrypted.is_not(None)))).all()
    for row in rows:
        assert row.api_key_encrypted is not None
        row.api_key_encrypted = new.seal(old.open(row.api_key_encrypted))
    return len(rows)


async def rekey_all(
    session_factory: async_sessionmaker[AsyncSession], old: SecretBox, new: SecretBox
) -> int:
    async with session_factory() as session:
        count = sum([await _rekey(session, model, old, new) for model in SEALED])
        await session.commit()
    return count
