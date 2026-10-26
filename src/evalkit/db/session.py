import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


def make_session_factory(db_url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(db_url)
    return async_sessionmaker(engine, expire_on_commit=False)


def run_migrations(db_url: str, revision: str = "head") -> None:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, revision)


MIGRATION_LOCK_KEY = 7_302_015_486_123_001


async def migrate_under_lock(db_url: str) -> None:
    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.connect() as conn:
        await conn.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        await asyncio.to_thread(run_migrations, db_url)
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
    await engine.dispose()


def downgrade_migrations(db_url: str, revision: str) -> None:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.downgrade(cfg, revision)


def head_revision() -> str:
    script = ScriptDirectory(str(Path(__file__).parent / "alembic"))
    head = script.get_current_head()
    assert head is not None
    return head


async def verify_schema(db_url: str) -> None:
    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current = result.scalar_one_or_none()
    except Exception as exc:
        raise RuntimeError(
            "auth schema not initialized — run `evalkit db upgrade --config <config>`"
        ) from exc
    finally:
        await engine.dispose()
    head = head_revision()
    if current != head:
        raise RuntimeError(
            f"auth schema is at revision {current!r}, expected {head!r} — "
            "run `evalkit db upgrade --config <config>`"
        )
