from __future__ import annotations

import os
import tempfile
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple

CONFIG_PATH = Path.home() / ".evalkit" / "config.toml"

DEFAULT_SERVER_URL = "https://evalkit.inferedge.com"

LOGIN_HINT = "`evalkit login` for hosted evalkit, `evalkit login --url <url>` for another server"

TOKEN_WARNING_WINDOW = timedelta(days=1)


class OidcLogin(NamedTuple):
    issuer: str
    client_id: str
    token_endpoint: str
    access_token: str
    refresh_token: str
    expires_at: datetime


class MachineLogin(NamedTuple):
    client_id: str
    client_secret: str


class Connection(NamedTuple):
    url: str
    token: str | None
    username: str | None
    from_env: bool = False
    oidc: OidcLogin | None = None
    machine: MachineLogin | None = None


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
                f"log in again to replace it: {LOGIN_HINT}"
            )
        return None
    oidc = None
    if "oidc" in data:
        raw = data["oidc"]
        oidc = OidcLogin(
            issuer=str(raw["issuer"]),
            client_id=str(raw["client_id"]),
            token_endpoint=str(raw["token_endpoint"]),
            access_token=str(raw["access_token"]),
            refresh_token=str(raw["refresh_token"]),
            expires_at=datetime.fromisoformat(str(raw["expires_at"])),
        )
    return Connection(str(data["url"]), data.get("token"), data.get("username"), oidc=oidc)


def save(connection: Connection, path: Path | None = None) -> Path:
    target = path or CONFIG_PATH
    lines = [f"url = {_toml_string(connection.url)}"]
    if connection.token is not None:
        lines.append(f"token = {_toml_string(connection.token)}")
    if connection.username is not None:
        lines.append(f"username = {_toml_string(connection.username)}")
    if connection.oidc is not None:
        lines += ["", "[oidc]"]
        lines += [
            f"{field} = {_toml_string(value.isoformat() if isinstance(value, datetime) else value)}"
            for field, value in connection.oidc._asdict().items()
        ]
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
        client_id = os.environ.get("EVALKIT_OIDC_CLIENT_ID")
        client_secret = os.environ.get("EVALKIT_OIDC_CLIENT_SECRET")
        if bool(client_id) != bool(client_secret):
            missing = "EVALKIT_OIDC_CLIENT_SECRET" if client_id else "EVALKIT_OIDC_CLIENT_ID"
            raise LookupError(f"{missing} is not set; client credentials need both")
        if client_id and client_secret:
            return Connection(
                env_url, None, None, from_env=True, machine=MachineLogin(client_id, client_secret)
            )
        return Connection(env_url, os.environ.get("EVALKIT_TOKEN"), None, from_env=True)
    stored = load(path or CONFIG_PATH)
    if stored is None:
        raise LookupError(f"no server configured — run {LOGIN_HINT}, or set EVALKIT_SERVER_URL")
    return stored


def renew_hint(connection: Connection) -> str:
    if connection.machine is not None:
        return "check EVALKIT_OIDC_CLIENT_ID and EVALKIT_OIDC_CLIENT_SECRET"
    if connection.from_env:
        return "set a fresh EVALKIT_TOKEN"
    return f"run `evalkit login --url {connection.url}`"


def token_expiry_warning(
    connection: Connection, expires_at: str | None, now: datetime
) -> str | None:
    if expires_at is None or connection.oidc is not None or connection.machine is not None:
        return None
    left = datetime.fromisoformat(expires_at) - now
    if left > TOKEN_WARNING_WINDOW:
        return None
    hours = max(left.total_seconds(), 0) / 3600
    return (
        f"the token for {connection.url} expires in {hours:.1f}h; "
        f"{renew_hint(connection)} before a long run"
    )
