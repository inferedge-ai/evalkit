import logging
from dataclasses import replace

from evalkit.api.access import TenantStores
from evalkit.config import Config
from evalkit.core.agents import AgentDefinition
from evalkit.core.endpoints import check_endpoint
from evalkit.core.runner import RunnerDeps
from evalkit.db.keys import StoredKeys

LOGGER = logging.getLogger(__name__)


async def server_keys(
    stores: TenantStores, cfg: Config, agent: AgentDefinition, judge_provider: str | None
) -> tuple[StoredKeys, list[str]]:
    keys = await stores.keys.load(agent, judge_provider)
    judge_url = keys.provider_judge(judge_provider)[0] if judge_provider is not None else None
    for url in (agent.harness_url, judge_url):
        if url is not None:
            await check_endpoint(url, allow_private=cfg.allow_private_endpoints)
    warnings = []
    if agent.harness_url is not None and keys.key_for("agent", agent.ref) is None:
        warnings.append(
            f"agent {agent.ref} has no API key; calling its harness without x-api-key. "
            f"Set one with evalkit agents edit {agent.ref} --api-key-env NAME"
        )
    for evaluator in (await stores.evaluators.in_workspace(agent.workspace)).values():
        if keys.key_for("evaluator", evaluator.ref) is None:
            warnings.append(
                f"evaluator {evaluator.ref} has no API key; calling it without x-api-key"
            )
    if judge_provider is not None and keys.provider_judge(judge_provider)[1] is None:
        warnings.append(f"LLM provider {judge_provider} has no API key; judging without one")
    for warning in warnings:
        LOGGER.warning("%s", warning)
    return keys, warnings


def with_keys(deps: RunnerDeps, keys: StoredKeys) -> RunnerDeps:
    return replace(deps, credentials=keys)
