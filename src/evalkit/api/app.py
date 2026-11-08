import asyncio
import logging
import math
import os
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx2
from fastapi import FastAPI, Request
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from evalkit import version
from evalkit.api import (
    agents as agents_api,
)
from evalkit.api import (
    builtin_evaluators,
    cli_dist,
    datasets,
    evaluators,
    experiments,
    fixtures,
    harness_check,
    imports,
    llm_providers,
    meta,
    otel,
    pull,
    review,
    runs,
    tokens,
    trace_imports,
    trace_sources,
    traces,
    users,
    workspaces,
)
from evalkit.api.auth import SIGNED_IN, bootstrap_admin
from evalkit.api.auth import router as auth_router
from evalkit.api.limits import BodyLimitMiddleware
from evalkit.api.mcp.auth import MCP_PATH
from evalkit.api.mcp.server import mcp_running, mount_mcp
from evalkit.api.oidc import OidcState
from evalkit.config import Config
from evalkit.core.agents import UnknownAgent
from evalkit.core.blob import BlobStore
from evalkit.core.endpoints import BlockedEndpoint
from evalkit.core.remote_evaluators import UnknownEvaluator
from evalkit.core.runner import (
    LEASE_SECONDS,
    RENEW_EVERY_SECONDS,
    AgentArchived,
    LeaseLost,
    RunnerDeps,
)
from evalkit.core.tenant import DEFAULT_TENANT_ID, BadTenantId
from evalkit.core.tenant import resolve_tenant as default_resolve_tenant
from evalkit.core.traces.sessions import SessionTooLarge
from evalkit.core.workspace import BareReference
from evalkit.credentials import MissingCredential
from evalkit.db.agents import AgentExists, AgentStore, InvalidAgent
from evalkit.db.cases import DatasetExists
from evalkit.db.crypto import SecretBox
from evalkit.db.evaluators import EvaluatorExists, InvalidEvaluator, RemoteEvaluatorStore
from evalkit.db.experiments import reap
from evalkit.db.keys import NoKeysLoaded
from evalkit.db.llm_providers import (
    LlmProviderExists,
    NotAProviderJudge,
    ProviderInUse,
    UnknownLlmProvider,
)
from evalkit.db.rekey import count_sealed
from evalkit.db.session import make_session_factory, migrate_under_lock, verify_schema
from evalkit.db.store import PgStore
from evalkit.db.trace_imports import UnknownTraceImport, reap_imports
from evalkit.db.trace_sources import (
    EncryptionKeyMissing,
    TraceSourceExists,
    UnknownTraceSource,
)
from evalkit.db.users import (
    IdentityConflict,
    InvalidName,
    InvalidPassword,
    LastAdmin,
    MachineUser,
    NameTaken,
    UnknownUser,
)
from evalkit.db.workspaces import PromotionLinkError, SharedOnly, UnknownWorkspace
from evalkit.paths import ui_root
from evalkit.store.base import Conflict, UnknownDataset, UnknownExperiment
from evalkit.store.blob import Obstore

LOGGER = logging.getLogger(__name__)


def content_security_policy(oidc: OidcState | None) -> str:
    connect_src = ["'self'"]
    if oidc is not None:
        urls = [oidc.cfg.issuer]
        if oidc.discovery is not None:
            urls.append(oidc.discovery.token_endpoint)
        for url in urls:
            parts = urlsplit(url)
            origin = f"{parts.scheme}://{parts.netloc}"
            if origin not in connect_src:
                connect_src.append(origin)
    return "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "font-src 'self' data:",
            "img-src 'self' data: blob:",
            "frame-src 'self' blob:",
            "worker-src 'self' blob:",
            f"connect-src {' '.join(connect_src)}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


TenantResolver = Callable[[Request, dict | None], Awaitable[str | None]]


def pod_holder() -> str:
    return f"pod:{os.environ.get('HOSTNAME') or socket.gethostname()}"


async def reap_forever(
    session_factory,
    *,
    interval: float = RENEW_EVERY_SECONDS,
    give_up_after: float = LEASE_SECONDS,
) -> None:
    failing_since: float | None = None
    while True:
        try:
            reaped = await reap(session_factory)
            reaped_imports = await reap_imports(session_factory)
        except Exception:
            now = time.monotonic()
            failing_since = now if failing_since is None else failing_since
            if now - failing_since > give_up_after:
                raise
            LOGGER.warning("lease reaper pass failed; retrying", exc_info=True)
        else:
            failing_since = None
            for exp_id in reaped:
                LOGGER.warning(
                    "experiment %s failed: its runner stopped renewing the lease", exp_id
                )
            for import_id in reaped_imports:
                LOGGER.warning(
                    "trace import %s failed: its task stopped renewing the lease", import_id
                )
        await asyncio.sleep(interval)


def stop_if_reaper_died(task: asyncio.Task) -> None:
    if task.cancelled() or task.exception() is None:
        return
    LOGGER.critical("the lease reaper stopped; shutting the server down", exc_info=task.exception())
    os.kill(os.getpid(), signal.SIGTERM)


def log_retry_failure(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        LOGGER.warning("OIDC discovery retry stopped", exc_info=exc)


def create_app(
    cfg: Config,
    *,
    blob_store: BlobStore | None = None,
    runner_deps: RunnerDeps | None = None,
    session_factory=None,
    check_schema: bool = True,
    ui_dist: Path | None = None,
    holder: str | None = None,
    oidc_transport: httpx2.AsyncBaseTransport | None = None,
    resolve_tenant: TenantResolver = default_resolve_tenant,
) -> FastAPI:
    resolved_blob_store = blob_store if blob_store is not None else Obstore(cfg.blob.url)
    session_factory = (
        session_factory
        if session_factory is not None
        else make_session_factory(
            cfg.db.url,
            pool_size=cfg.db.pool_size,
            max_overflow=cfg.db.max_overflow,
            pool_timeout=cfg.db.pool_timeout_seconds,
        )
    )
    box = SecretBox(cfg.encryption_key) if cfg.encryption_key else None
    resolved_deps = (
        runner_deps
        if runner_deps is not None
        else RunnerDeps(
            cfg=cfg,
            store=PgStore(session_factory, DEFAULT_TENANT_ID),
            blob_store=resolved_blob_store,
            agents=AgentStore(session_factory, DEFAULT_TENANT_ID),
            evaluators=RemoteEvaluatorStore(session_factory, DEFAULT_TENANT_ID),
            credentials=NoKeysLoaded(),
        )
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Migrations are an explicit operator step (`evalkit db upgrade`) unless
        # db.migrate_on_start opts in; then they run under an advisory lock so
        # replicas never race. Either way the server verifies the schema and
        # fails fast when it is missing or outdated.
        if cfg.db.migrate_on_start:
            await migrate_under_lock(cfg.db.url)
        if check_schema:
            await verify_schema(cfg.db.url)
        if cfg.encryption_key is None and await count_sealed(app.state.session_factory):
            raise RuntimeError(
                "stored API keys exist but encryption_key is not configured; "
                "set encryption_key: env:EVALKIT_ENCRYPTION_KEY in the server config"
            )
        await bootstrap_admin(app.state.session_factory, os.environ.get("EVALKIT_ADMIN_PASSWORD"))
        if app.state.oidc is not None and not await app.state.oidc.load():
            app.state.oidc_retry = asyncio.create_task(app.state.oidc.retry_forever())
            app.state.oidc_retry.add_done_callback(log_retry_failure)
        reaper = asyncio.create_task(reap_forever(app.state.session_factory))
        reaper.add_done_callback(stop_if_reaper_died)
        async with mcp_running(mcp_app):
            yield
        reaper.cancel()
        await asyncio.gather(reaper, return_exceptions=True)
        if app.state.oidc_retry is not None:
            app.state.oidc_retry.cancel()
            await asyncio.gather(app.state.oidc_retry, return_exceptions=True)
        if app.state.oidc is not None:
            await app.state.oidc.http.aclose()
        await experiments.interrupt_tasks(app.state.session_factory, app.state.tasks)
        await trace_imports.interrupt_imports(app.state.session_factory, app.state.import_tasks)
        await resolved_deps.store.aclose()
        engine = app.state.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="evalkit", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_exception_handler(StarletteHTTPException, otel.log_rejection)
    app.state.cfg = cfg
    app.state.resolve_tenant = resolve_tenant
    app.state.runner_deps = resolved_deps
    app.state.holder = holder or pod_holder()
    app.state.session_factory = session_factory
    app.state.box = box
    app.state.blob_store = resolved_blob_store
    app.state.tasks = {}
    app.state.import_tasks = {}
    app.state.oidc = (
        OidcState(
            cfg.auth.oidc,
            httpx2.AsyncClient(transport=oidc_transport, timeout=10.0),
            f"{cfg.server.base_url.rstrip('/')}{MCP_PATH}",
        )
        if cfg.auth.oidc is not None
        else None
    )
    app.state.oidc_retry = None

    running_version = version.current()

    app.add_exception_handler(SessionTooLarge, traces.session_too_large)

    @app.exception_handler(BadTenantId)
    async def bad_tenant_id(request: Request, exc: BadTenantId) -> JSONResponse:
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(UnknownDataset)
    async def unknown_dataset(request: Request, exc: UnknownDataset) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(UnknownExperiment)
    @app.exception_handler(UnknownWorkspace)
    @app.exception_handler(UnknownUser)
    @app.exception_handler(UnknownAgent)
    @app.exception_handler(UnknownEvaluator)
    @app.exception_handler(UnknownTraceSource)
    @app.exception_handler(UnknownTraceImport)
    @app.exception_handler(UnknownLlmProvider)
    async def unknown_name(request: Request, exc: KeyError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.args[0]})

    @app.exception_handler(NameTaken)
    @app.exception_handler(IdentityConflict)
    @app.exception_handler(LastAdmin)
    @app.exception_handler(AgentExists)
    @app.exception_handler(EvaluatorExists)
    @app.exception_handler(TraceSourceExists)
    @app.exception_handler(AgentArchived)
    @app.exception_handler(Conflict)
    @app.exception_handler(LeaseLost)
    @app.exception_handler(PromotionLinkError)
    @app.exception_handler(DatasetExists)
    @app.exception_handler(LlmProviderExists)
    @app.exception_handler(ProviderInUse)
    async def conflict(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(BareReference)
    @app.exception_handler(SharedOnly)
    @app.exception_handler(InvalidName)
    @app.exception_handler(InvalidPassword)
    @app.exception_handler(MachineUser)
    @app.exception_handler(InvalidEvaluator)
    @app.exception_handler(InvalidAgent)
    @app.exception_handler(MissingCredential)
    @app.exception_handler(EncryptionKeyMissing)
    @app.exception_handler(BlockedEndpoint)
    @app.exception_handler(NotAProviderJudge)
    async def bad_request(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(PoolTimeout)
    async def pool_exhausted(request: Request, exc: PoolTimeout) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "the server's database connections are all in use; retry"},
            headers={"Retry-After": str(math.ceil(cfg.db.pool_timeout_seconds))},
        )

    app.add_middleware(BodyLimitMiddleware, cfg=cfg)

    @app.middleware("http")
    async def stamp_version(request, call_next):
        response = await call_next(request)
        response.headers["X-Evalkit-Version"] = running_version
        response.headers.update(SECURITY_HEADERS)
        response.headers["Content-Security-Policy"] = content_security_policy(app.state.oidc)
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    app.include_router(cli_dist.router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    app.include_router(auth_router)
    app.include_router(meta.appearance_router)
    app.include_router(datasets.router, dependencies=SIGNED_IN)
    app.include_router(meta.router, dependencies=SIGNED_IN)
    app.include_router(experiments.router, dependencies=SIGNED_IN)
    app.include_router(experiments.runs_router, dependencies=SIGNED_IN)
    app.include_router(agents_api.router, dependencies=SIGNED_IN)
    app.include_router(evaluators.router, dependencies=SIGNED_IN)
    app.include_router(builtin_evaluators.router, dependencies=SIGNED_IN)
    app.include_router(trace_sources.router, dependencies=SIGNED_IN)
    app.include_router(llm_providers.router, dependencies=SIGNED_IN)
    app.include_router(runs.router, dependencies=SIGNED_IN)
    app.include_router(pull.router, dependencies=SIGNED_IN)
    app.include_router(harness_check.router, dependencies=SIGNED_IN)
    app.include_router(imports.router, dependencies=SIGNED_IN)
    app.include_router(review.router, dependencies=SIGNED_IN)
    app.include_router(fixtures.router, dependencies=SIGNED_IN)
    app.include_router(users.router, dependencies=SIGNED_IN)
    app.include_router(workspaces.router, dependencies=SIGNED_IN)
    app.include_router(tokens.router, dependencies=SIGNED_IN)
    app.include_router(traces.router, dependencies=SIGNED_IN)
    app.include_router(trace_imports.router, dependencies=SIGNED_IN)
    app.include_router(otel.router)
    mcp_app = mount_mcp(app)

    if ui_dist is None and ui_root().is_dir():
        ui_dist = ui_root()
    if ui_dist is not None:
        app.frontend("/", directory=ui_dist)
    return app
