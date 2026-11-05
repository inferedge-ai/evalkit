import logging
from dataclasses import replace

from fastapi import Request

from evalkit.config import Config
from evalkit.core.agents import AgentDefinition
from evalkit.core.endpoints import check_endpoint
from evalkit.core.runner import RunnerDeps
from evalkit.db.keys import KeyLoader, StoredKeys

LOGGER = logging.getLogger(__name__)


async def server_keys(
    request: Request, agent: AgentDefinition, judge_base_url: str | None
) -> tuple[StoredKeys, list[str]]:
    cfg: Config = request.app.state.cfg
    deps: RunnerDeps = request.app.state.runner_deps
    loader: KeyLoader = request.app.state.keys
    for url in (agent.harness_url, judge_base_url):
        if url is not None:
            await check_endpoint(url, allow_private=cfg.allow_private_endpoints)
    keys = await loader.load(agent, judge_base_url)
    warnings = []
    if agent.harness_url is not None and keys.key_for("agent", agent.ref) is None:
        warnings.append(
            f"agent {agent.ref} has no API key; calling its harness without x-api-key. "
            f"Set one with evalkit agents edit {agent.ref} --api-key-env NAME"
        )
    for evaluator in (await deps.evaluators.in_workspace(agent.workspace)).values():
        if keys.key_for("evaluator", evaluator.ref) is None:
            warnings.append(
                f"evaluator {evaluator.ref} has no API key; calling it without x-api-key"
            )
    if judge_base_url is not None and keys.require_judge_key(judge_base_url) is None:
        warnings.append(f"LLM provider {judge_base_url} has no API key; judging without one")
    for warning in warnings:
        LOGGER.warning("%s", warning)
    return keys, warnings


def with_keys(deps: RunnerDeps, keys: StoredKeys) -> RunnerDeps:
    return replace(deps, credentials=keys)
