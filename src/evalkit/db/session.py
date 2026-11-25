import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Final

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
    session_factory: async_sessionmaker[AsyncSession], tenant_id: str | object
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


ALEMBIC_ROOT = Path(__file__).parent / "alembic"
EVALKIT_BRANCH = "evalkit"
JOBS_VERSIONS = "procrastinate.contrib.alembic:versions"
JOBS_TABLE_PREFIX = "procrastinate_"
EXTENSION_MIGRATIONS_GROUP = "evalkit.migrations"


def include_name(name: str | None, type_: str, parent_names: Mapping[Any, str | None]) -> bool:
    return not (type_ == "table" and name is not None and name.startswith(JOBS_TABLE_PREFIX))


def version_locations() -> list[str]:
    extensions = sorted(point.value for point in entry_points(group=EXTENSION_MIGRATIONS_GROUP))
    return [str(ALEMBIC_ROOT / "versions"), JOBS_VERSIONS, *extensions]


def alembic_config(db_url: str | None = None) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(ALEMBIC_ROOT))
    cfg.set_main_option("path_separator", "newline")
    cfg.set_main_option("version_locations", "\n".join(version_locations()))
    if db_url is not None:
        cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


def run_migrations(db_url: str, revision: str = "heads") -> None:
    command.upgrade(alembic_config(db_url), "heads" if revision == "head" else revision)


MIGRATION_LOCK_KEY = 7_302_015_486_123_001
MIGRATION_LOCK_POLL_SECONDS = 0.5


async def migrate_under_lock(db_url: str) -> None:
    # The job queue's migrations run CREATE INDEX CONCURRENTLY, which waits for
    # every open transaction in the database. So the lock is held in AUTOCOMMIT
    # and a waiting server polls for it: a blocking pg_advisory_lock is itself a
    # transaction the index build would wait on forever.
    engine = create_async_engine(db_url, poolclass=NullPool, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        while not await conn.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
        ):
            await asyncio.sleep(MIGRATION_LOCK_POLL_SECONDS)
        await asyncio.to_thread(run_migrations, db_url)
        await conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
    await engine.dispose()


def downgrade_migrations(db_url: str, revision: str) -> None:
    target = f"{EVALKIT_BRANCH}@base" if revision == "base" else revision
    command.downgrade(alembic_config(db_url), target)


def head_revisions() -> set[str]:
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())


def head_revision() -> str:
    head = ScriptDirectory.from_config(alembic_config()).get_revision(f"{EVALKIT_BRANCH}@head")
    assert head is not None
    return head.revision


async def verify_schema(db_url: str) -> None:
    engine = create_async_engine(db_url)
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT version_num FROM alembic_version"))
            current = set(result.scalars().all())
    except Exception as exc:
        raise RuntimeError(
            "auth schema not initialized — run `evalkit db upgrade --config <config>`"
        ) from exc
    finally:
        await engine.dispose()
    heads = head_revisions()
    if current != heads:
        raise RuntimeError(
            f"auth schema is at revision {sorted(current)!r}, expected {sorted(heads)!r} — "
            "run `evalkit db upgrade --config <config>`"
        )
