import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Final

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.pool import NullPool

from evalkit.db.models import ROOT_MODELS

ALL_TENANTS: Final = object()


def scope_to_tenant(state: ORMExecuteState) -> None:
    info = state.session.info
    if "tenant_id" not in info:
        raise RuntimeError("session has no tenant")
    tenant_id = info["tenant_id"]
    if tenant_id is ALL_TENANTS:
        return
    if not (state.is_select or state.is_update or state.is_delete):
        return
    for model in ROOT_MODELS:
        state.statement = state.statement.options(
            with_loader_criteria(
                model, lambda cls: cls.tenant_id == tenant_id, include_aliases=True
            )
        )


def register_tenant_guard() -> None:
    if not event.contains(Session, "do_orm_execute", scope_to_tenant):
        event.listen(Session, "do_orm_execute", scope_to_tenant)


@asynccontextmanager
async def tenant_session(
    session_factory: async_sessionmaker[AsyncSession], tenant_id: int | object
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        session.info["tenant_id"] = tenant_id
        yield session


def make_session_factory(
    db_url: str, *, pool_size: int = 20, max_overflow: int = 10, pool_timeout: float = 5
) -> async_sessionmaker[AsyncSession]:
    register_tenant_guard()
    engine = create_async_engine(
        db_url, pool_size=pool_size, max_overflow=max_overflow, pool_timeout=pool_timeout
    )
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
