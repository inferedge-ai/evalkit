from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

import httpx2

from evalkit import version
from evalkit.client.retry import Retry, RetryTransport

RETRY_STATUSES = (502, 503, 504)
RETRY_ERRORS = (httpx2.NetworkError, httpx2.ConnectTimeout, httpx2.RemoteProtocolError)
RETRY_ATTEMPTS = 4
RETRY_BACKOFF_FACTOR = 0.5
RETRY_MAX_WAIT_SECONDS = 8.0
READ_METHODS = ("GET", "HEAD")
WRITE_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH")


class ApiRequestError(Exception):
    def __init__(self, status: int, detail: str, method: str, path: str):
        self.status = status
        self.detail = detail
        super().__init__(f"{method} {path} → {status}: {detail}")

    @classmethod
    def from_response(cls, resp: httpx2.Response) -> ApiRequestError:
        return cls(
            resp.status_code, response_detail(resp), resp.request.method, resp.request.url.path
        )


def response_detail(resp: httpx2.Response) -> str:
    if resp.content and resp.headers.get("content-type", "").startswith("application/json"):
        body = resp.json()
        if isinstance(body, dict) and body.get("detail") is not None:
            return str(body["detail"])
    return resp.reason_phrase


def retry_policy(methods: tuple[str, ...]) -> Retry:
    return Retry(
        total=RETRY_ATTEMPTS,
        allowed_methods=methods,
        status_forcelist=RETRY_STATUSES,
        retry_on_exceptions=RETRY_ERRORS,
        backoff_factor=RETRY_BACKOFF_FACTOR,
        max_backoff_wait=RETRY_MAX_WAIT_SECONDS,
    )


def retry_extensions(retry: bool | None) -> dict[str, Retry]:
    if retry is None:
        return {}
    return {"retry": retry_policy(WRITE_METHODS) if retry else Retry(total=0)}


class VersionMismatch(Exception):
    def __init__(self, local: str, remote: str):
        self.local = local
        self.remote = remote
        super().__init__(
            f"evalkit {local} does not match server {remote}; local runs need the same "
            "version. Run `evalkit update` from the project directory: it reinstalls the CLI "
            "and moves the evalkit-py pin in ./pyproject.toml"
        )


# Shorter than uvicorn's 5s idle keep-alive, so a poll never reuses a
# connection the server is closing at the same moment.
KEEPALIVE_EXPIRY_SECONDS = 2
DEFAULT_TIMEOUT_SECONDS = 120


def timeout_seconds() -> float:
    return float(os.environ.get("EVALKIT_TIMEOUT", DEFAULT_TIMEOUT_SECONDS))


def encode(segment: str) -> str:
    return quote(segment, safe="")


class AsyncApi:
    def __init__(
        self,
        server_url: str,
        auth: httpx.Auth | None = None,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ):
        self._client = httpx2.AsyncClient(
            base_url=server_url.rstrip("/"),
            auth=auth,
            timeout=timeout_seconds(),
            transport=RetryTransport(
                transport=transport
                or httpx2.AsyncHTTPTransport(
                    limits=httpx2.Limits(keepalive_expiry=KEEPALIVE_EXPIRY_SECONDS)
                ),
                retry=retry_policy(READ_METHODS),
            ),
        )
        self.remote_version: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self, method: str, path: str, *, retry: bool | None = None, **kwargs
    ) -> httpx2.Response:
        resp = await self._client.request(
            method, path, extensions=retry_extensions(retry), **kwargs
        )
        self.remote_version = resp.headers.get("X-Evalkit-Version", self.remote_version)
        if resp.status_code >= 400:
            raise ApiRequestError.from_response(resp)
        return resp

    async def get(self, path: str, **kwargs) -> httpx2.Response:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs) -> httpx2.Response:
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs) -> httpx2.Response:
        return await self.request("PUT", path, **kwargs)

    async def patch(self, path: str, **kwargs) -> httpx2.Response:
        return await self.request("PATCH", path, **kwargs)

    async def head(self, path: str, **kwargs) -> httpx2.Response:
        resp = await self._client.request("HEAD", path, **kwargs)
        self.remote_version = resp.headers.get("X-Evalkit-Version", self.remote_version)
        return resp

    async def require_same_version(self) -> None:
        await self.get("/api/health")
        local = version.current()
        if self.remote_version != local:
            raise VersionMismatch(local, self.remote_version or "unknown")


class SyncApi:
    def __init__(
        self,
        server_url: str,
        auth: httpx.Auth | None = None,
        *,
        transport: httpx2.BaseTransport | None = None,
    ):
        self._client = httpx2.Client(
            base_url=server_url.rstrip("/"),
            auth=auth,
            timeout=timeout_seconds(),
            transport=RetryTransport(
                transport=transport or httpx2.HTTPTransport(), retry=retry_policy(READ_METHODS)
            ),
        )

    def _handle(self, resp: httpx2.Response) -> httpx2.Response:
        if resp.status_code >= 400:
            raise ApiRequestError.from_response(resp)
        return resp

    def request(
        self, method: str, path: str, *, retry: bool | None = None, **kwargs
    ) -> httpx2.Response:
        return self._handle(
            self._client.request(method, path, extensions=retry_extensions(retry), **kwargs)
        )

    def get(self, path: str, **kwargs) -> httpx2.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx2.Response:
        return self.request("POST", path, **kwargs)

    def delete(self, path: str, **kwargs) -> httpx2.Response:
        return self.request("DELETE", path, **kwargs)

    def post_files(self, path: str, files: list[tuple[str, Path]], **kwargs) -> httpx2.Response:
        parts = [("files", (name, f.read_bytes(), "application/octet-stream")) for name, f in files]
        return self.request("POST", path, files=parts, **kwargs)

    def patch(self, path: str, **kwargs) -> httpx2.Response:
        return self.request("PATCH", path, **kwargs)

    def put(self, path: str, **kwargs) -> httpx2.Response:
        return self.request("PUT", path, **kwargs)

    def head(self, path: str, **kwargs) -> httpx2.Response:
        return self._client.request("HEAD", path, **kwargs)
