"""Server-side CLI command bodies.

Everything here needs the `evalkit[server]` extra (uvicorn, FastAPI, SQLAlchemy,
alembic). The base CLI install stays a thin HTTP client; `main.py` imports this
module only when the server extra is present and points users at it otherwise.
"""

import uvicorn

from evalkit.api.app import create_app
from evalkit.config import Config
from evalkit.db.crypto import SecretBox
from evalkit.db.session import make_session_factory, run_migrations
from evalkit.db.trace_sources import TraceSourceStore


def serve(config: Config, host: str, port: int) -> None:
    uvicorn.run(create_app(config), host=host, port=port)


def db_upgrade(config: Config, revision: str) -> None:
    run_migrations(config.db.url, revision)


async def rekey_trace_sources(config: Config, old_key: str, new_key: str) -> int:
    factory = make_session_factory(config.db.url)
    count = await TraceSourceStore(factory, None).rekey(SecretBox(old_key), SecretBox(new_key))
    await factory.kw["bind"].dispose()
    return count
