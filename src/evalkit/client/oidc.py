from __future__ import annotations

import base64
import hashlib
import secrets
import threading
import time
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2

from evalkit.client import connection
from evalkit.client.api import response_detail
from evalkit.client.connection import Connection, MachineLogin, OidcLogin

REFRESH_WINDOW_SECONDS = 60
LOOPBACK_TIMEOUT_SECONDS = 300.0
LOOPBACK_PAGE = b"<!doctype html><title>evalkit</title><p>Logged in. You can close this tab."
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


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
        transport: httpx2.BaseTransport | None = None,
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
        with httpx2.Client(transport=self.transport, timeout=30.0) as http:
            resp = http.post(
                login.token_endpoint,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": login.refresh_token,
                    "client_id": login.client_id,
                },
            )
        if resp.status_code in (400, 401):
            return None
        resp.raise_for_status()
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
        if fresh is None or fresh.oidc is None or fresh.url != self.conn.url:
            return None
        if fresh.oidc.access_token == login.access_token:
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
        transport: httpx2.BaseTransport | None = None,
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
                with httpx2.Client(transport=self.transport, timeout=30.0) as http:
                    resp = http.post(
                        self.token_endpoint,
                        data={"grant_type": "client_credentials", **self.authorize_params},
                        auth=httpx2.BasicAuth(self.machine.client_id, self.machine.client_secret),
                    )
                resp.raise_for_status()
                tokens = resp.json()
                self.token = tokens["access_token"]
                self.expires_at = expiry_from(tokens)
            assert self.token is not None
            return self.token


class EvalkitAuth(httpx2.Auth):
    def __init__(self, source: TokenSource) -> None:
        self.source = source

    def auth_flow(
        self, request: httpx2.Request
    ) -> Generator[httpx2.Request, httpx2.Response, None]:
        request.headers["Authorization"] = f"Bearer {self.source.bearer()}"
        yield request


def fetch_providers(url: str, transport: httpx2.BaseTransport | None = None) -> dict:
    with httpx2.Client(base_url=url.rstrip("/"), transport=transport, timeout=30.0) as http:
        resp = http.get("/api/auth/providers")
        resp.raise_for_status()
        return resp.json()


def static_auth(token: str) -> EvalkitAuth:
    return EvalkitAuth(StaticToken(token))


def build_auth(
    conn: Connection,
    *,
    path: Path | None = None,
    transport: httpx2.BaseTransport | None = None,
) -> EvalkitAuth | None:
    if conn.machine is not None:
        oidc = fetch_providers(conn.url, transport)["oidc"]
        if oidc is None:
            raise LookupError(
                "EVALKIT_OIDC_CLIENT_ID is set but the server has no identity provider configured"
            )
        return EvalkitAuth(
            ClientCredentials(
                conn.machine, oidc["token_endpoint"], oidc["authorize_params"], transport
            )
        )
    if conn.oidc is not None:
        return EvalkitAuth(StoredOidcLogin(conn, path=path, transport=transport))
    if conn.token is not None:
        return static_auth(conn.token)
    return None


class LoginTimedOut(Exception):
    def __init__(self) -> None:
        super().__init__("login timed out; run `evalkit login` again")


class NoDeviceGrant(Exception):
    def __init__(self) -> None:
        super().__init__(
            "this identity provider has no device authorization endpoint; "
            "log in from a machine with a browser"
        )


class LoginRefused(Exception):
    pass


def generate_verifier() -> str:
    return secrets.token_urlsafe(48)


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def run_loopback_flow(
    providers_oidc: dict,
    open_browser: Callable[[str], None],
    *,
    timeout: float = LOOPBACK_TIMEOUT_SECONDS,
    transport: httpx2.BaseTransport | None = None,
) -> dict:
    received: dict[str, str] = {}
    done = threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if urlsplit(self.path).path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            received.update({k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(LOOPBACK_PAGE)
            done.set()

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer(("127.0.0.1", 0), Callback)
    redirect_uri = f"http://127.0.0.1:{server.server_address[1]}/callback"
    verifier = generate_verifier()
    state = secrets.token_urlsafe(16)
    query = urlencode(
        {
            "response_type": "code",
            "client_id": providers_oidc["client_id"],
            "redirect_uri": redirect_uri,
            "scope": " ".join(providers_oidc["scopes"]),
            "state": state,
            "code_challenge": pkce_challenge(verifier),
            "code_challenge_method": "S256",
            **providers_oidc["authorize_params"],
        }
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    open_browser(f"{providers_oidc['authorization_endpoint']}?{query}")
    finished = done.wait(timeout)
    server.shutdown()
    server.server_close()
    if not finished:
        raise LoginTimedOut()
    if received.get("state") != state:
        raise LoginRefused("state mismatch on the login callback")
    if "error" in received:
        raise LoginRefused(
            f"{received['error']}: {received.get('error_description', 'login refused')}"
        )
    if "code" not in received:
        raise LoginRefused("the login callback carried no code")
    with httpx2.Client(transport=transport, timeout=30.0) as http:
        resp = http.post(
            providers_oidc["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": received["code"],
                "redirect_uri": redirect_uri,
                "client_id": providers_oidc["client_id"],
                "code_verifier": verifier,
            },
        )
    resp.raise_for_status()
    return resp.json()


def run_device_flow(
    providers_oidc: dict,
    echo: Callable[[str], None],
    *,
    sleep: Callable[[float], None] = time.sleep,
    transport: httpx2.BaseTransport | None = None,
) -> dict:
    endpoint = providers_oidc["device_authorization_endpoint"]
    if endpoint is None:
        raise NoDeviceGrant()
    with httpx2.Client(transport=transport, timeout=30.0) as http:
        started_resp = http.post(
            endpoint,
            data={
                "client_id": providers_oidc["client_id"],
                "scope": " ".join(providers_oidc["scopes"]),
                **providers_oidc["authorize_params"],
            },
        )
        started_resp.raise_for_status()
        started = started_resp.json()
        echo(f"open {started['verification_uri']} and enter {started['user_code']}")
        interval = float(started.get("interval", 5))
        deadline = time.monotonic() + float(started.get("expires_in", 300))
        while time.monotonic() < deadline:
            resp = http.post(
                providers_oidc["token_endpoint"],
                data={
                    "grant_type": DEVICE_GRANT,
                    "device_code": started["device_code"],
                    "client_id": providers_oidc["client_id"],
                },
            )
            if resp.status_code == 200:
                return resp.json()
            if not resp.headers.get("content-type", "").startswith("application/json"):
                raise LoginRefused(f"device authorization failed: HTTP {resp.status_code}")
            error = resp.json().get("error") if resp.status_code == 400 else None
            if error == "slow_down":
                interval += 5
            elif error != "authorization_pending":
                raise LoginRefused(error or response_detail(resp))
            sleep(interval)
    raise LoginTimedOut()


def register_identity(
    url: str, tokens: dict, transport: httpx2.BaseTransport | None = None
) -> dict:
    with httpx2.Client(base_url=url.rstrip("/"), transport=transport, timeout=30.0) as http:
        resp = http.post(
            "/api/auth/oidc/register",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
            json={"id_token": tokens["id_token"]},
        )
    if resp.status_code >= 400:
        raise LookupError(response_detail(resp))
    return resp.json()


def login_from_tokens(url: str, providers_oidc: dict, tokens: dict) -> Connection:
    return Connection(
        url,
        None,
        None,
        oidc=OidcLogin(
            issuer=providers_oidc["issuer"],
            client_id=providers_oidc["client_id"],
            token_endpoint=providers_oidc["token_endpoint"],
            access_token=tokens["access_token"],
            refresh_token=tokens["refresh_token"],
            expires_at=expiry_from(tokens),
        ),
    )
