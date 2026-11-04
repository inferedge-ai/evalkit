from __future__ import annotations

import threading
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from evalkit.client import connection
from evalkit.client.connection import Connection, MachineLogin, OidcLogin

REFRESH_WINDOW_SECONDS = 60


class LoginExpired(Exception):
    def __init__(self, url: str):
        super().__init__(f"your login has expired; run `evalkit login --url {url}`")


class TokenSource(Protocol):
    def bearer(self) -> str: ...


class StaticToken:
    def __init__(self, token: str) -> None:
        self.token = token

    def bearer(self) -> str:
        return self.token


def expires_within(expires_at: datetime, seconds: int) -> bool:
    return expires_at - datetime.now(UTC) < timedelta(seconds=seconds)


def expiry_from(tokens: dict[str, Any]) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=int(tokens["expires_in"]))


class StoredOidcLogin:
    def __init__(
        self,
        conn: Connection,
        path: Path | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        assert conn.oidc is not None
        self.conn = conn
        self.path = path
        self.transport = transport
        self.lock = threading.Lock()

    def bearer(self) -> str:
        with self.lock:
            login = self.conn.oidc
            assert login is not None
            if not expires_within(login.expires_at, REFRESH_WINDOW_SECONDS):
                return login.access_token
            refreshed = self.refresh(login)
            if refreshed is None:
                refreshed = self.adopt_newer(login)
            if refreshed is None:
                raise LoginExpired(self.conn.url)
            self.conn = self.conn._replace(oidc=refreshed)
            return refreshed.access_token

    def refresh(self, login: OidcLogin) -> OidcLogin | None:
        with httpx.Client(transport=self.transport, timeout=30.0) as http:
            resp = http.post(
                login.token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": login.refresh_token,
                    "client_id": login.client_id,
                },
            )
        if resp.status_code != 200:
            return None
        tokens = resp.json()
        refreshed = login._replace(
            access_token=tokens["access_token"],
            refresh_token=tokens.get("refresh_token", login.refresh_token),
            expires_at=expiry_from(tokens),
        )
        connection.save(self.conn._replace(oidc=refreshed), path=self.path)
        return refreshed

    def adopt_newer(self, login: OidcLogin) -> OidcLogin | None:
        fresh = connection.load(self.path or connection.CONFIG_PATH)
        if fresh is None or fresh.oidc is None or fresh.oidc.access_token == login.access_token:
            return None
        if expires_within(fresh.oidc.expires_at, REFRESH_WINDOW_SECONDS):
            return None
        return fresh.oidc


class ClientCredentials:
    def __init__(
        self,
        machine: MachineLogin,
        token_endpoint: str,
        authorize_params: dict[str, str],
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.machine = machine
        self.token_endpoint = token_endpoint
        self.authorize_params = authorize_params
        self.transport = transport
        self.token: str | None = None
        self.expires_at = datetime.now(UTC)
        self.lock = threading.Lock()

    def bearer(self) -> str:
        with self.lock:
            if self.token is None or expires_within(self.expires_at, REFRESH_WINDOW_SECONDS):
                with httpx.Client(transport=self.transport, timeout=30.0) as http:
                    resp = http.post(
                        self.token_endpoint,
                        data={"grant_type": "client_credentials", **self.authorize_params},
                        auth=httpx.BasicAuth(self.machine.client_id, self.machine.client_secret),
                    )
                resp.raise_for_status()
                tokens = resp.json()
                self.token = tokens["access_token"]
                self.expires_at = expiry_from(tokens)
            assert self.token is not None
            return self.token


class EvalkitAuth(httpx.Auth):
    def __init__(self, source: TokenSource) -> None:
        self.source = source

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.source.bearer()}"
        yield request
