import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from croniter import croniter
from procrastinate import Blueprint, JobContext
from procrastinate.exceptions import AlreadyEnqueued
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.core.agents import AgentDefinition
from evalkit.core.app_runs import AppRunDeps, store_app_runs
from evalkit.db.app_runs import agents_with_app_runs

LOGGER = logging.getLogger(__name__)

TICK_CRON = "* * * * *"
SERVICES = "app_runs"
NAMESPACE = "app_runs"
STORE_TASK = f"{NAMESPACE}:store"
TICK_TASK = f"{NAMESPACE}:tick"


@dataclass(frozen=True)
class AppRunServices:
    session_factory: async_sessionmaker[AsyncSession]
    deps: Callable[[str], AppRunDeps]
    agent: Callable[[str, int], Awaitable[AgentDefinition]]


def agent_lock(agent_id: int) -> str:
    return f"app-runs:{agent_id}"


async def store(context: JobContext, tenant_id: str, agent_id: int) -> None:
    services: AppRunServices = context.additional_context[SERVICES]
    agent = await services.agent(tenant_id, agent_id)
    result = await store_app_runs(services.deps(tenant_id), agent)
    LOGGER.log(
        logging.WARNING if result.stopped else logging.INFO,
        "stored app runs of agent %s: %s stored, %s not finished yet, %s failed%s",
        agent.ref,
        result.stored,
        result.waiting,
        result.failed,
        f"; stopped: {result.stopped}" if result.stopped else "",
    )


async def tick(context: JobContext, timestamp: int) -> None:
    services: AppRunServices = context.additional_context[SERVICES]
    minute = datetime.fromtimestamp(timestamp, UTC)
    for agent in await agents_with_app_runs(services.session_factory):
        if not croniter.match(agent.cron, minute):
            continue
        lock = agent_lock(agent.agent_id)
        # A pass for this agent is already waiting: one is enough.
        try:
            await context.app.configure_task(STORE_TASK, lock=lock, queueing_lock=lock).defer_async(
                tenant_id=agent.tenant_id, agent_id=agent.agent_id
            )
        except AlreadyEnqueued:
            continue


# A blueprint joins one app only, so every app gets its own.
def build_blueprint() -> Blueprint:
    blueprint = Blueprint()
    blueprint.task(name="store", pass_context=True)(store)
    blueprint.periodic(cron=TICK_CRON)(
        blueprint.task(name="tick", queueing_lock="app-runs-tick", pass_context=True)(tick)
    )
    return blueprint
