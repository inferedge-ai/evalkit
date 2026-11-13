import os
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from testcontainers.community.postgres import PostgresContainer

from evalkit.db.session import make_session_factory, run_migrations, tenant_session


def _with_database(url: str, name: str) -> str:
    return make_url(url).set(database=name).render_as_string(hide_password=False)


def _admin(server_url: str, sql: str) -> None:
    admin = create_engine(server_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(sql))
    admin.dispose()


def _database(server_url: str, template: str | None) -> Iterator[str]:
    name = f"t_{uuid.uuid4().hex[:16]}"
    clause = f' TEMPLATE "{template}"' if template else ""
    _admin(server_url, f'CREATE DATABASE "{name}"{clause}')
    yield _with_database(server_url, name)
    _admin(server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(scope="session")
def pg_server_url() -> Iterator[str]:
    url = os.environ.get("EVALKIT_TEST_PG_URL")
    if url:
        yield url
        return
    container = PostgresContainer("postgres:17", driver="psycopg").with_command(
        "postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off"
        " -c authentication_timeout=5s"
    )
    with container:
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def pg_template(pg_server_url: str, worker_id: str) -> str:
    name = f"evalkit_tpl_{worker_id}"
    _admin(pg_server_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    _admin(pg_server_url, f'CREATE DATABASE "{name}"')
    run_migrations(_with_database(pg_server_url, name))
    return name


@pytest.fixture
def fresh_db_url(pg_server_url: str) -> Iterator[str]:
    yield from _database(pg_server_url, None)


@pytest.fixture
def db_url(pg_server_url: str, pg_template: str) -> Iterator[str]:
    yield from _database(pg_server_url, pg_template)


@pytest.fixture(scope="module")
def module_db_url(pg_server_url: str, pg_template: str) -> Iterator[str]:
    yield from _database(pg_server_url, pg_template)


class TenantFactory:
    def __init__(self, factory: async_sessionmaker[AsyncSession], tenant_id: str):
        self.factory = factory
        self.tenant_id = tenant_id
        self.kw = factory.kw

    def __call__(self):
        return tenant_session(self.factory, self.tenant_id)


@pytest.fixture
async def raw_session_factory(db_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    factory = make_session_factory(db_url)
    yield factory
    await factory.kw["bind"].dispose()


@pytest.fixture
async def session_factory(raw_session_factory) -> TenantFactory:
    return TenantFactory(raw_session_factory, "default")
