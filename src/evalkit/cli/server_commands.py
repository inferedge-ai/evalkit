"""Server-side CLI command bodies.

Everything here needs the `evalkit-py[server]` extra (uvicorn, FastAPI, SQLAlchemy,
alembic). The base CLI install stays a thin HTTP client; `main.py` imports this
module only when the server extra is present and points users at it otherwise.
"""

import asyncio

import psycopg
import sentry_sdk
import uvicorn
from cryptography.fernet import Fernet
from psycopg import sql
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from sqlalchemy.engine import make_url

from evalkit import version
from evalkit.api.app import create_app
from evalkit.cli.client import ApiError
from evalkit.config import Config
from evalkit.contracts.settings import JudgeSettings
from evalkit.db.crypto import SecretBox
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.rekey import rekey_all
from evalkit.db.session import make_session_factory, run_migrations


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


async def rekey(config: Config, old_key: str, new_key: str) -> int:
    factory = make_session_factory(config.db.url)
    count = await rekey_all(factory, SecretBox(old_key), SecretBox(new_key))
    await factory.kw["bind"].dispose()
    return count


def create_database_if_missing(url: str) -> bool:
    parsed = make_url(url).set(drivername="postgresql")
    if not parsed.database:
        raise ValueError(f"{url} names no database")
    try:
        psycopg.connect(parsed.render_as_string(hide_password=False)).close()
        return False
    except psycopg.OperationalError as exc:
        if f'database "{parsed.database}" does not exist' not in str(exc):
            raise ApiError(
                f"could not connect to Postgres at {parsed.host or 'localhost'}:"
                f"{parsed.port or 5432}: {str(exc).strip()}\n"
                "Is Postgres running (brew services start postgresql@17, sudo systemctl "
                "start postgresql, Postgres.app), and are the user and password in the URL "
                "right? See Try it locally > When something fails."
            ) from exc
    admin = parsed.set(database="postgres").render_as_string(hide_password=False)
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(parsed.database)))
    return True


def init_database(url: str) -> bool:
    created = create_database_if_missing(url)
    run_migrations(url, "head")
    return created


TRY_PROVIDER = "try"


def new_encryption_key() -> str:
    return Fernet.generate_key().decode()


async def _register_default_judge(
    url: str, encryption_key: str, base_url: str, api_key: str, model: str
) -> None:
    factory = make_session_factory(url)
    store = LlmProviderStore(factory)
    sealed = SecretBox(encryption_key).seal(api_key)
    existing = await store.active_by_base_url(base_url)
    if existing is None:
        await store.create(TRY_PROVIDER, base_url, sealed)
    else:
        await store.edit(existing.name, api_key_encrypted=sealed)
    judge = JudgeSettings.model_validate({"base_url": base_url, "model": model, "concurrency": 4})
    await store.set_default_judge(judge)
    await factory.kw["bind"].dispose()


def register_default_judge(
    url: str, encryption_key: str, base_url: str, api_key: str, model: str
) -> None:
    asyncio.run(_register_default_judge(url, encryption_key, base_url, api_key, model))
