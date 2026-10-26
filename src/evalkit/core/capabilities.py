from evalkit.contracts.harness import Accepts, Capabilities, Limits, Provides
from evalkit.core.agents import AgentDefinition

SDK_CONTRACT_VERSION = "sdk"
TRACED_FLAGS = ("trajectory", "tool_calls", "token_usage")


def sdk_capabilities(agent: AgentDefinition) -> Capabilities:
    provides = {
        "trajectory": True,
        "tool_calls": True,
        "token_usage": True,
        "thinking": False,
        "artifacts": False,
        "workspace": False,
        "run_listing": True,
        **(agent.settings.provides or {}),
    }
    return Capabilities(
        contract_version=SDK_CONTRACT_VERSION,
        agent=agent.name,
        provides=Provides(**provides),
        accepts=Accepts(files=True, model_override=True, file_urls=False, stop=False),
        limits=Limits(max_concurrent_runs=agent.settings.max_concurrent_runs),
    )


def traced_capabilities(caps: Capabilities, agent: AgentDefinition) -> Capabilities:
    if not agent.is_otel:
        return caps
    provides = {
        **caps.provides.model_dump(),
        **dict.fromkeys(TRACED_FLAGS, True),
        **(agent.settings.provides or {}),
    }
    return caps.model_copy(update={"provides": Provides(**provides)})
