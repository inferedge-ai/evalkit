import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from starlette.applications import Starlette
from starlette.routing import Route

from evalkit import version
from evalkit.api.mcp import tools
from evalkit.api.mcp.auth import MCP_PATH, build_auth
from evalkit.api.mcp.descriptions import INSTRUCTIONS
from evalkit.core.agents import UnknownAgent
from evalkit.db.workspaces import UnknownWorkspace
from evalkit.store.base import UnknownExperiment

LOGGER = logging.getLogger(__name__)
LOGGED_ARGS_CHARS = 2000

STATUS_CODES = {
    400: "invalid_request",
    401: "forbidden",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "invalid_request",
}


def translate(exc: BaseException | None) -> ToolError | None:
    if isinstance(exc, HTTPException):
        return ToolError(f"{STATUS_CODES.get(exc.status_code, 'upstream_failed')}: {exc.detail}")
    if isinstance(exc, (UnknownAgent, UnknownExperiment, UnknownWorkspace)):
        return ToolError(f"not_found: {exc.args[0]}")
    return None


# FastMCP masks a tool's exception into a generic ToolError before middleware
# runs; the original is its __cause__.
class ToolErrors(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        token = get_access_token()
        caller = token.claims.get("sub") if token else None
        name = context.message.name
        arguments = json.dumps(context.message.arguments or {}, default=str)[:LOGGED_ARGS_CHARS]
        started = time.monotonic()
        outcome = "ok"
        try:
            return await call_next(context)
        except ToolError as exc:
            translated = translate(exc.__cause__) or exc
            outcome = str(translated).split(":", 1)[0]
            if translated is exc:
                raise
            raise translated from exc.__cause__
        finally:
            LOGGER.info(
                "mcp tool %s by %s: %s in %dms args=%s",
                name,
                caller,
                outcome,
                (time.monotonic() - started) * 1000,
                arguments,
            )


def build_mcp(app: FastAPI, jwt_verifier: TokenVerifier | None = None) -> FastMCP:
    mcp = FastMCP(
        "evalkit",
        version=version.current(),
        instructions=INSTRUCTIONS,
        auth=build_auth(app.state.cfg, app.state.session_factory, jwt_verifier),
        middleware=[ToolErrors()],
        mask_error_details=True,
    )
    tools.register(mcp, app.state)
    return mcp


# Only the MCP endpoint and its RFC 9728 metadata are routed to the MCP app,
# so /api keeps its own auth and the SPA fallback never sees /mcp.
def mount_mcp(app: FastAPI, jwt_verifier: TokenVerifier | None = None):
    mcp_app = build_mcp(app, jwt_verifier).http_app(
        path=MCP_PATH,
        transport="streamable-http",
        stateless_http=True,
        json_response=True,
        host_origin_protection=False,
    )
    for route in mcp_app.routes:
        if isinstance(route, Route):
            app.router.routes.append(Route(route.path, mcp_app, include_in_schema=False))
    return mcp_app


# The session manager's task group must open and close in one task; a test
# client may enter and leave the app lifespan from different ones.
@asynccontextmanager
async def mcp_running(mcp_app: Starlette) -> AsyncIterator[None]:
    started = asyncio.Event()
    stop = asyncio.Event()

    async def hold() -> None:
        async with mcp_app.router.lifespan_context(mcp_app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(hold())
    ready = asyncio.create_task(started.wait())
    await asyncio.wait({task, ready}, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        ready.cancel()
        task.result()
    try:
        yield
    finally:
        stop.set()
        await task
