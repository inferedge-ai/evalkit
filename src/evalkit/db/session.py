import asyncio
import warnings
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path
from typing import Any, Final

from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import Script, ScriptDirectory
from alembic.util import coerce_resource_to_filename
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria
from sqlalchemy.pool import NullPool

from evalkit.db.models import ROOT_MODELS, Base

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


def list_extension_points() -> list[EntryPoint]:
    return sorted(entry_points(group=EXTENSION_MIGRATIONS_GROUP), key=lambda point: point.name)


def resolve_extension_folder(point: EntryPoint) -> Path:
    folder = coerce_resource_to_filename(point.value).resolve()
    if not folder.is_dir():
        raise RuntimeError(
            f"entry point {point.name!r} in group {EXTENSION_MIGRATIONS_GROUP!r} names "
            f"{point.value!r}, but {folder} is not a folder — fix the entry point, or ship "
            "the folder of revisions in the package"
        )
    return folder


def extension_locations() -> list[str]:
    return [str(resolve_extension_folder(point)) for point in list_extension_points()]


# Which tables of the database autogenerate compares with evalkit's models: not the
# job queue's, and, when a package has added its branch, not a table evalkit has no
# model for, which is that package's.
def include_name(name: str | None, type_: str, parent_names: Mapping[Any, str | None]) -> bool:
    if type_ != "table" or name is None:
        return True
    if name.startswith(JOBS_TABLE_PREFIX):
        return False
    return name in Base.metadata.tables or not list_extension_points()


def version_locations() -> list[str]:
    return [str(ALEMBIC_ROOT / "versions"), JOBS_VERSIONS, *extension_locations()]


def alembic_config(db_url: str | None = None) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(ALEMBIC_ROOT))
    cfg.set_main_option("path_separator", "newline")
    cfg.set_main_option("version_locations", "\n".join(version_locations()))
    if db_url is not None:
        cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


DUPLICATE_REVISION_WARNING = "is present more than once"


def check_extension_branch(point: EntryPoint, revisions: list[Script]) -> None:
    folder = resolve_extension_folder(point)
    for script in revisions:
        if Path(script.path).parent != folder or script.down_revision is not None:
            continue
        if not script.branch_labels:
            raise RuntimeError(
                f"revision {script.revision!r} of entry point {point.name!r} in group "
                f"{EXTENSION_MIGRATIONS_GROUP!r} has no `branch_labels`: the first revision "
                "of a package's branch carries a branch label of the package's own"
            )


def load_scripts() -> ScriptDirectory:
    scripts = ScriptDirectory.from_config(alembic_config())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        scripts.get_heads()
    for warning in caught:
        if DUPLICATE_REVISION_WARNING in str(warning.message):
            raise RuntimeError(
                f"{warning.message}: every revision id must be unique across evalkit and "
                "the packages installed with it — rename the package's revision"
            )
        warnings.warn_explicit(warning.message, warning.category, warning.filename, warning.lineno)
    revisions = list(scripts.walk_revisions())
    for point in list_extension_points():
        check_extension_branch(point, revisions)
    return scripts


def run_migrations(db_url: str, revision: str = "heads") -> None:
    load_scripts()
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
    load_scripts()
    command.downgrade(alembic_config(db_url), target)


def head_revisions() -> set[str]:
    return set(load_scripts().get_heads())


def known_revisions() -> set[str]:
    return {script.revision for script in load_scripts().walk_revisions()}


def head_revision() -> str:
    head = load_scripts().get_revision(f"{EVALKIT_BRANCH}@head")
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
    unknown = current - known_revisions()
    if unknown:
        raise RuntimeError(
            f"the database is at revision {sorted(unknown)!r}, which this install does not "
            "have: it was applied by a newer evalkit or by a package not installed here — "
            "install that evalkit version or that package again"
        )
    heads = head_revisions()
    if current != heads:
        raise RuntimeError(
            f"auth schema is at revision {sorted(current)!r}, expected {sorted(heads)!r} — "
            "run `evalkit db upgrade --config <config>`"
        )
