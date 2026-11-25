import asyncio
import logging
import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

import httpx2
from starlette.datastructures import State

from evalkit.api.access import build_tenant_stores
from evalkit.api.trace_imports import import_new_traces
from evalkit.core.agents import AgentDefinition
from evalkit.core.app_runs import HARNESS_RETRY, AppRunDeps
from evalkit.core.costmap import load_cost_map
from evalkit.core.harness import HarnessClient
from evalkit.core.retry import RetryTransport
from evalkit.db.agents import AgentStore
from evalkit.db.app_runs import PgAppRunStore
from evalkit.jobs.app import build_job_app
from evalkit.jobs.app_runs import SERVICES, AppRunServices

LOGGER = logging.getLogger(__name__)

SHUTDOWN_SECONDS = 30


def app_run_services(state: State) -> AppRunServices:
    def deps(tenant_id: str) -> AppRunDeps:
        stores = build_tenant_stores(state, tenant_id)

        async def harness(agent: AgentDefinition) -> HarnessClient:
            transport = stores.deps.harness_transport or httpx2.AsyncHTTPTransport()
            return HarnessClient(
                agent,
                await stores.keys.agent_key(agent),
                transport=RetryTransport(transport, HARNESS_RETRY),
            )

        async def import_traces(agent: AgentDefinition, cursor: datetime, now: datetime) -> None:
            await import_new_traces(state, stores, agent, cursor, now)

        return AppRunDeps(
            store=PgAppRunStore(state.session_factory, tenant_id),
            sessions=stores.sessions,
            blob_store=stores.blob,
            cost_map=load_cost_map(state.cfg.cost_map),
            settle_seconds=stores.sessions.settle_seconds,
            harness=harness,
            import_traces=import_traces,
        )

    async def agent(tenant_id: str, agent_id: int) -> AgentDefinition:
        return await AgentStore(state.session_factory, tenant_id).get_by_id(agent_id)

    return AppRunServices(session_factory=state.session_factory, deps=deps, agent=agent)


def stop_if_worker_died(task: asyncio.Task) -> None:
    if task.cancelled() or task.exception() is None:
        return
    LOGGER.critical("the job worker stopped; shutting the server down", exc_info=task.exception())
    os.kill(os.getpid(), signal.SIGTERM)


@asynccontextmanager
async def embedded_worker(state: State) -> AsyncIterator[None]:
    if not state.cfg.worker.embedded:
        yield
        return
    job_app = build_job_app(state.session_factory.kw["bind"].url, state.cfg.worker)
    async with job_app.open_async():
        worker = asyncio.create_task(
            job_app.run_worker_async(
                install_signal_handlers=False,
                additional_context={SERVICES: app_run_services(state)},
            )
        )
        worker.add_done_callback(stop_if_worker_died)
        try:
            yield
        finally:
            worker.cancel()
            await asyncio.wait([worker], timeout=SHUTDOWN_SECONDS)
