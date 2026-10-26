from __future__ import annotations

import os
import tempfile
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

CONFIG_PATH = Path.home() / ".evalkit" / "config.toml"

TOKEN_WARNING_WINDOW = timedelta(days=1)


class Connection(NamedTuple):
    url: str
    token: str | None
    username: str | None
    from_env: bool = False


def _toml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def load(path: Path | None = None) -> Connection | None:
    target = path or CONFIG_PATH
    if not target.is_file():
        return None
    data = tomllib.loads(target.read_text())
    if "url" not in data:
        if "profiles" in data:
            raise LookupError(
                f"{target} holds server profiles, which evalkit no longer supports; "
                "run `evalkit login --url <url>` to replace it"
            )
        return None
    return Connection(str(data["url"]), data.get("token"), data.get("username"))


def save(connection: Connection, path: Path | None = None) -> Path:
    target = path or CONFIG_PATH
    lines = [f"url = {_toml_string(connection.url)}"]
    if connection.token is not None:
        lines.append(f"token = {_toml_string(connection.token)}")
    if connection.username is not None:
        lines.append(f"username = {_toml_string(connection.username)}")
    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    fd, temp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.")
    with os.fdopen(fd, "w") as file:
        file.write("\n".join(lines) + "\n")
    os.replace(temp, target)
    return target


def active(path: Path | None = None) -> Connection:
    env_url = os.environ.get("EVALKIT_SERVER_URL")
    if env_url:
        return Connection(env_url, os.environ.get("EVALKIT_TOKEN"), None, from_env=True)
    stored = load(path or CONFIG_PATH)
    if stored is None:
        raise LookupError(
            "no server configured — run `evalkit login --url <url>` or set EVALKIT_SERVER_URL"
        )
    return stored


def renew_hint(connection: Connection) -> str:
    if connection.from_env:
        return "set a fresh EVALKIT_TOKEN"
    return f"run `evalkit login --url {connection.url}`"


def token_expiry_warning(
    connection: Connection, expires_at: str | None, now: datetime
) -> str | None:
    if expires_at is None:
        return None
    left = datetime.fromisoformat(expires_at) - now
    if left > TOKEN_WARNING_WINDOW:
        return None
    hours = max(left.total_seconds(), 0) / 3600
    return (
        f"the token for {connection.url} expires in {hours:.1f}h; "
        f"{renew_hint(connection)} before a long run"
    )
