import logging
from collections.abc import Set
from dataclasses import replace

from evalkit.api.access import TenantStores
from evalkit.core.agents import AgentDefinition
from evalkit.core.endpoints import EndpointRule, check_endpoint
from evalkit.core.runner import RunnerDeps
from evalkit.db.keys import StoredKeys

LOGGER = logging.getLogger(__name__)


async def server_keys(
    stores: TenantStores,
    agent: AgentDefinition,
    judge_provider: str | None,
    *,
    rule: EndpointRule,
    evaluators_called: Set[str] = frozenset(),
) -> tuple[StoredKeys, list[str]]:
    keys = await stores.keys.load(agent, judge_provider)
    if agent.harness_url is not None:
        await check_endpoint(agent.harness_url, rule=rule, owner=f"agent {agent.ref}")
    if judge_provider is not None:
        await check_endpoint(
            keys.provider_judge(judge_provider)[0],
            rule=rule,
            owner=f"LLM provider {judge_provider} (an admin fixes it with "
            f"evalkit llm-providers edit {judge_provider} --base-url <url>)",
        )
    evaluators = (await stores.evaluators.in_workspace(agent.workspace)).values()
    for evaluator in evaluators:
        if evaluator.name not in evaluators_called:
            continue
        await check_endpoint(
            evaluator.settings.endpoint, rule=rule, owner=f"evaluator {evaluator.ref}"
        )
    warnings = []
    if agent.harness_url is not None and keys.key_for("agent", agent.ref) is None:
        warnings.append(
            f"agent {agent.ref} has no API key; calling its harness without x-api-key. "
            f"Set one with evalkit agents edit {agent.ref} --api-key-env NAME"
        )
    for evaluator in evaluators:
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
