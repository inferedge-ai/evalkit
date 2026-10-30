"""Server-side CLI command bodies.

Everything here needs the `evalkit[server]` extra (uvicorn, FastAPI, SQLAlchemy,
alembic). The base CLI install stays a thin HTTP client; `main.py` imports this
module only when the server extra is present and points users at it otherwise.
"""

import psycopg
import sentry_sdk
import uvicorn
from psycopg import sql
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sqlalchemy.engine import make_url

from evalkit import version
from evalkit.api.app import create_app
from evalkit.config import Config
from evalkit.db.crypto import SecretBox
from evalkit.db.session import make_session_factory, run_migrations
from evalkit.db.trace_sources import TraceSourceStore


def init_sentry(config: Config) -> None:
    if config.sentry is None:
        return
    sentry_sdk.init(
        dsn=config.sentry.dsn,
        environment=config.sentry.environment,
        release=f"evalkit@{version.current()}",
        traces_sample_rate=config.sentry.traces_sample_rate,
        send_default_pii=False,
        max_request_body_size=config.sentry.max_request_body_size,
        integrations=[
            StarletteIntegration(failed_request_status_codes={500}),
            FastApiIntegration(failed_request_status_codes={500}),
        ],
    )


def serve(config: Config, host: str, port: int) -> None:
    init_sentry(config)
    uvicorn.run(create_app(config), host=host, port=port)


def db_upgrade(config: Config, revision: str) -> None:
    run_migrations(config.db.url, revision)


async def rekey_trace_sources(config: Config, old_key: str, new_key: str) -> int:
    factory = make_session_factory(config.db.url)
    count = await TraceSourceStore(factory, None).rekey(SecretBox(old_key), SecretBox(new_key))
    await factory.kw["bind"].dispose()
    return count


def create_database_if_missing(url: str) -> None:
    parsed = make_url(url).set(drivername="postgresql")
    try:
        psycopg.connect(parsed.render_as_string(hide_password=False)).close()
        return
    except psycopg.OperationalError as exc:
        if "does not exist" not in str(exc):
            raise
    admin = parsed.set(database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(parsed.database)))


def init_database(url: str) -> None:
    create_database_if_missing(url)
    run_migrations(url, "head")
