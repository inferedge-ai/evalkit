from __future__ import annotations

from collections.abc import Callable

import httpx

from evalkit import version
from evalkit.client.agents import HttpAgents
from evalkit.client.api import AsyncApi
from evalkit.client.blob import HttpBlobStore
from evalkit.client.connection import Connection
from evalkit.client.evaluators import HttpEvaluators
from evalkit.client.sessions import HttpSessionReader
from evalkit.client.store import HttpStore, local_holder
from evalkit.config import LocalConfig
from evalkit.contracts.enums import ExperimentStatus
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.agents import explicit
from evalkit.core.harness_check import check_harness
from evalkit.core.runner import (
    ExperimentRequest,
    RunnerDeps,
    dropped_cases_text,
    harness_capabilities,
    rescore_experiment,
    run_experiment,
)
from evalkit.core.workspace import Reference, slug
from evalkit.credentials import CredentialRegistry
from evalkit.store.base import GitRevision, Selection


def credentials(local: LocalConfig, conn: Connection) -> CredentialRegistry:
    return CredentialRegistry(local.credentials, local.environ(), home=conn.username)


def holder_for(conn: Connection) -> str:
    return local_holder(conn.username or "unknown")


def _deps(
    local: LocalConfig,
    conn: Connection,
    api: AsyncApi,
    exp_id: int,
    progress: Callable[[str], None],
    harness_transport: httpx.AsyncBaseTransport | None,
    custom_eval_transport: httpx.AsyncBaseTransport | None,
) -> RunnerDeps:
    return RunnerDeps(
        cfg=local,
        store=HttpStore(api),
        blob_store=HttpBlobStore(api, exp_id),
        agents=HttpAgents(api),
        evaluators=HttpEvaluators(api),
        credentials=credentials(local, conn),
        harness_transport=harness_transport,
        custom_eval_transport=custom_eval_transport,
        progress=progress,
        sessions=HttpSessionReader(api),
    )


async def run_local(
    conn: Connection,
    local: LocalConfig,
    *,
    agent: Reference,
    dataset_slug: str,
    selection: Selection,
    repetitions: int,
    model: str | None,
    resume: int | None,
    revision: GitRevision,
    progress: Callable[[str], None],
    judge: JudgeSettings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    custom_eval_transport: httpx.AsyncBaseTransport | None = None,
    api_transport: httpx.AsyncBaseTransport | None = None,
) -> int:
    api = AsyncApi(conn.url, conn.token, transport=api_transport)
    holder = holder_for(conn)
    try:
        await api.require_same_version()
        store = HttpStore(api)
        if resume is None:
            definition = await HttpAgents(api).get(explicit(agent))
            caps = await harness_capabilities(
                definition, credentials(local, conn).key_for("agent", definition.ref), transport
            )
            exp, dropped = await store.start_experiment(
                ExperimentRequest(
                    dataset=dataset_slug,
                    agent=slug(agent),
                    selection=selection,
                    repetitions=repetitions,
                    model=model,
                    capabilities=caps,
                    cases=revision,
                    evalkit_version=version.current(),
                    judge=judge,
                    holder=holder,
                )
            )
            if dropped:
                progress(dropped_cases_text(dropped))
        else:
            exp = await store.transition(resume, ExperimentStatus.RUNNING, holder)
        progress(f"experiment {exp.id} (#{exp.number} of {exp.dataset})")
        deps = _deps(local, conn, api, exp.id, progress, transport, custom_eval_transport)
        await run_experiment(deps, exp.id, holder)
        return exp.id
    finally:
        await api.aclose()


async def evaluate_local(
    conn: Connection,
    local: LocalConfig,
    exp_id: int,
    *,
    progress: Callable[[str], None],
    custom_eval_transport: httpx.AsyncBaseTransport | None = None,
    api_transport: httpx.AsyncBaseTransport | None = None,
    refetch_trajectories: bool = False,
) -> None:
    api = AsyncApi(conn.url, conn.token, transport=api_transport)
    holder = holder_for(conn)
    try:
        await api.require_same_version()
        deps = _deps(local, conn, api, exp_id, progress, None, custom_eval_transport)
        await deps.store.transition(exp_id, ExperimentStatus.EVALUATING, holder)
        await rescore_experiment(deps, exp_id, holder, refetch_trajectories=refetch_trajectories)
    finally:
        await api.aclose()


async def mark_interrupted(conn: Connection, exp_id: int, user: str) -> None:
    api = AsyncApi(conn.url, conn.token)
    try:
        await HttpStore(api).transition(
            exp_id,
            ExperimentStatus.FAILED,
            holder_for(conn),
            error=f"interrupted: cancelled by {user}",
        )
    finally:
        await api.aclose()


async def local_input_schema(
    conn: Connection,
    local: LocalConfig,
    agent: Reference,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    api_transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    api = AsyncApi(conn.url, conn.token, transport=api_transport)
    try:
        definition = await HttpAgents(api).get(explicit(agent))
    finally:
        await api.aclose()
    key = credentials(local, conn).key_for("agent", definition.ref)
    caps = await harness_capabilities(definition, key, transport)
    return caps.effective_input_schema


async def check_local(
    conn: Connection,
    local: LocalConfig,
    agent: Reference,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    api_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, dict]:
    api = AsyncApi(conn.url, conn.token, transport=api_transport)
    try:
        blob_store = HttpBlobStore(api, None)
        definition = await HttpAgents(api).get(explicit(agent))
        return await check_harness(
            definition,
            credentials(local, conn).key_for("agent", definition.ref),
            blob_store,
            transport=transport,
            sessions=HttpSessionReader(api),
            agent_slug=slug(agent),
        )
    finally:
        await api.aclose()
