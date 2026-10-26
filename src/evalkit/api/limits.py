import logging
import re
from dataclasses import dataclass

from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from evalkit.config import MB, Config

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class BodyLimit:
    what: str
    key: str
    limit: int

    def detail(self) -> str:
        return (
            f"{self.what} is larger than the {_size(self.limit)} limit (server config {self.key})"
        )


def _size(n: int) -> str:
    return f"{n // MB} MB" if n % MB == 0 else f"{n} bytes"


def body_limit(cfg: Config, method: str, path: str) -> BodyLimit:
    limits = cfg.limits
    if method == "PUT" and re.fullmatch(r"/api/fixtures/sha256/[^/]+", path):
        return BodyLimit(
            "fixture upload", "limits.fixture_upload_bytes", limits.fixture_upload_bytes
        )
    if method == "PUT" and re.fullmatch(r"/api/experiments/\d+/blob/.+", path):
        return BodyLimit(
            "experiment blob", "limits.experiment_blob_bytes", limits.experiment_blob_bytes
        )
    if method == "POST" and path in ("/api/sync", "/api/lint"):
        return BodyLimit("case upload", "limits.case_upload_bytes", limits.case_upload_bytes)
    if method == "POST" and path == "/api/otel/v1/traces":
        return BodyLimit("trace batch", "otel.max_batch_bytes", cfg.otel.max_batch_bytes)
    return BodyLimit("request body", "limits.request_bytes", limits.request_bytes)


def _log_rejection(limit: BodyLimit, method: str, path: str, received: int) -> None:
    LOGGER.warning(
        "request body too large: route=%s %s limit=%s key=%s received=%s",
        method,
        path,
        limit.limit,
        limit.key,
        received,
    )


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp, cfg: Config) -> None:
        self.app = app
        self.cfg = cfg

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        limit = body_limit(self.cfg, method, path)
        declared = dict(scope["headers"]).get(b"content-length", b"")
        received = 0

        async def capped() -> Message:
            nonlocal received
            if declared.isdigit() and int(declared) > limit.limit:
                _log_rejection(limit, method, path, int(declared))
                raise HTTPException(status_code=413, detail=limit.detail())
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit.limit:
                    _log_rejection(limit, method, path, received)
                    raise HTTPException(status_code=413, detail=limit.detail())
            return message

        await self.app(scope, capped, send)
