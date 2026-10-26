from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_name(session: AsyncSession, namespace: str, name: str = "") -> None:
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(f"{namespace}:{name}"))))
