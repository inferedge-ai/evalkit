import asyncio
import logging
import os
import signal
import socket
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastmcp.server.auth import TokenVerifier
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from evalkit import version
from evalkit.api import (
    agents as agents_api,
)
from evalkit.api import (
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
from evalkit.api.auth import bootstrap_admin, current_user
from evalkit.api.auth import router as auth_router
from evalkit.api.limits import BodyLimitMiddleware
from evalkit.api.mcp.server import mcp_running, mount_mcp
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
from evalkit.core.traces.sessions import SessionTooLarge
from evalkit.core.workspace import BareReference
from evalkit.credentials import MissingCredential
from evalkit.db.agents import AgentExists, AgentStore, InvalidAgent
from evalkit.db.cases import DatasetExists
from evalkit.db.crypto import SecretBox
from evalkit.db.evaluators import EvaluatorExists, InvalidEvaluator, RemoteEvaluatorStore
from evalkit.db.experiments import reap
from evalkit.db.keys import KeyLoader, NoKeysLoaded
from evalkit.db.llm_providers import (
    LlmProviderExists,
    LlmProviderStore,
    ProviderInUse,
    UnknownLlmProvider,
)
from evalkit.db.mappings import MappingStore
from evalkit.db.rekey import count_sealed
from evalkit.db.review import ReviewLog
from evalkit.db.session import make_session_factory, migrate_under_lock, verify_schema
from evalkit.db.sessions import DbSessionReader
from evalkit.db.spans import SpanStore
from evalkit.db.store import PgStore
from evalkit.db.tokens import AgentTokens
from evalkit.db.trace_imports import TraceImportStore, UnknownTraceImport, reap_imports
from evalkit.db.trace_sources import (
    EncryptionKeyMissing,
    TraceSourceExists,
    TraceSourceStore,
    UnknownTraceSource,
)
from evalkit.db.users import InvalidName, InvalidPassword, LastAdmin, NameTaken, UnknownUser
from evalkit.db.workspaces import PromotionLinkError, SharedOnly, UnknownWorkspace
from evalkit.store.base import Conflict, UnknownDataset, UnknownExperiment
from evalkit.store.blob import Obstore

LOGGER = logging.getLogger(__name__)

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self' data:",
        "img-src 'self' data: blob:",
        "frame-src 'self' blob:",
        "worker-src 'self' blob:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


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


def create_app(
    cfg: Config,
    *,
    blob_store: BlobStore | None = None,
    runner_deps: RunnerDeps | None = None,
    agents: AgentStore | None = None,
    session_factory=None,
    check_schema: bool = True,
    ui_dist: Path | None = None,
    wheel_dir: Path | None = None,
    holder: str | None = None,
    mcp_jwt_verifier: TokenVerifier | None = None,
) -> FastAPI:
    resolved_blob_store = blob_store if blob_store is not None else Obstore(cfg.blob.url)
    session_factory = (
        session_factory if session_factory is not None else make_session_factory(cfg.db.url)
    )
    resolved_store = PgStore(session_factory)
    resolved_agents = agents if agents is not None else AgentStore(session_factory)
    resolved_spans = SpanStore(session_factory)
    resolved_mappings = MappingStore(session_factory)
    resolved_sessions = DbSessionReader(
        resolved_agents, resolved_spans, resolved_mappings, cfg.otel
    )
    resolved_deps = (
        runner_deps
        if runner_deps is not None
        else RunnerDeps(
            cfg=cfg,
            store=resolved_store,
            blob_store=resolved_blob_store,
            agents=resolved_agents,
            evaluators=RemoteEvaluatorStore(session_factory),
            credentials=NoKeysLoaded(),
            sessions=resolved_sessions,
        )
    )
    if resolved_deps.sessions is None:
        resolved_deps.sessions = resolved_sessions
    box = SecretBox(cfg.encryption_key) if cfg.encryption_key else None
    key_loader = KeyLoader(session_factory, box)
    if resolved_deps.agent_keys is None:
        resolved_deps.agent_keys = key_loader

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
        reaper = asyncio.create_task(reap_forever(app.state.session_factory))
        reaper.add_done_callback(stop_if_reaper_died)
        async with mcp_running(mcp_app):
            yield
        reaper.cancel()
        await asyncio.gather(reaper, return_exceptions=True)
        await experiments.interrupt_tasks(resolved_store, app.state.tasks)
        await trace_imports.interrupt_imports(app.state.trace_imports, app.state.import_tasks)
        await resolved_deps.store.aclose()
        engine = app.state.session_factory.kw.get("bind")
        if engine is not None:
            await engine.dispose()

    app = FastAPI(title="evalkit", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_exception_handler(StarletteHTTPException, otel.log_rejection)
    app.state.cfg = cfg
    app.state.runner_deps = resolved_deps
    app.state.review_log = ReviewLog(session_factory)
    app.state.store = resolved_store
    app.state.holder = holder or pod_holder()
    app.state.agents = resolved_agents
    app.state.tokens = AgentTokens(session_factory)
    app.state.spans = resolved_spans
    app.state.mappings = resolved_mappings
    app.state.sessions = resolved_sessions
    app.state.session_factory = session_factory
    app.state.trace_sources = TraceSourceStore(session_factory, box)
    app.state.keys = key_loader
    app.state.llm_providers = LlmProviderStore(session_factory)
    app.state.tasks = {}
    app.state.trace_imports = TraceImportStore(session_factory)
    app.state.import_tasks = {}

    env_wheel_dir = os.environ.get("EVALKIT_WHEEL_DIR")
    if wheel_dir is None and env_wheel_dir:
        wheel_dir = Path(env_wheel_dir)
    running_version = version.current()
    app.state.served_wheel = cli_dist.load_wheel(wheel_dir, running_version)

    app.add_exception_handler(SessionTooLarge, traces.session_too_large)

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
    @app.exception_handler(InvalidEvaluator)
    @app.exception_handler(InvalidAgent)
    @app.exception_handler(MissingCredential)
    @app.exception_handler(EncryptionKeyMissing)
    @app.exception_handler(BlockedEndpoint)
    async def bad_request(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.add_middleware(BodyLimitMiddleware, cfg=cfg)

    @app.middleware("http")
    async def stamp_version(request, call_next):
        response = await call_next(request)
        response.headers["X-Evalkit-Version"] = running_version
        response.headers.update(SECURITY_HEADERS)
        if request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    app.include_router(cli_dist.router)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True}

    app.include_router(auth_router)
    wall = [Depends(current_user)]
    app.include_router(datasets.router, dependencies=wall)
    app.include_router(meta.router, dependencies=wall)
    app.include_router(experiments.router, dependencies=wall)
    app.include_router(experiments.runs_router, dependencies=wall)
    app.include_router(agents_api.router, dependencies=wall)
    app.include_router(evaluators.router, dependencies=wall)
    app.include_router(trace_sources.router, dependencies=wall)
    app.include_router(llm_providers.router, dependencies=wall)
    app.include_router(runs.router, dependencies=wall)
    app.include_router(pull.router, dependencies=wall)
    app.include_router(harness_check.router, dependencies=wall)
    app.include_router(imports.router, dependencies=wall)
    app.include_router(review.router, dependencies=wall)
    app.include_router(fixtures.router, dependencies=wall)
    app.include_router(users.router, dependencies=wall)
    app.include_router(workspaces.router, dependencies=wall)
    app.include_router(tokens.router, dependencies=wall)
    app.include_router(traces.router, dependencies=wall)
    app.include_router(trace_imports.router, dependencies=wall)
    app.include_router(otel.router)
    mcp_app = mount_mcp(app, mcp_jwt_verifier)

    if ui_dist is None:
        ui_dist = Path(__file__).resolve().parents[3] / "ui" / "dist"
    if ui_dist.is_dir():
        assets = ui_dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")
        index = ui_dist / "index.html"
        root = ui_dist.resolve()

        # SPA history fallback: real files are served as-is; any other
        # non-API path gets index.html so client-side routes survive a
        # refresh or a direct link. Unmatched /api/* paths stay 404s.
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            if path.startswith(("api/", "assets/")):
                raise HTTPException(status_code=404)
            candidate = (ui_dist / path).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index)

    return app
