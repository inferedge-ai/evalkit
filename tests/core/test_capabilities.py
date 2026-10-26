from evalkit.contracts.enums import TrajectorySource
from evalkit.contracts.harness import Capabilities
from evalkit.contracts.settings import AgentSettings
from evalkit.core.capabilities import sdk_capabilities, traced_capabilities
from tests.fake_agents import fake_agent


def test_sdk_snapshot_defaults_and_overrides():
    agent = fake_agent(
        "sdk",
        harness_url=None,
        settings=AgentSettings(
            max_concurrent_runs=3, provides={"thinking": True, "tool_calls": False}
        ),
    )
    caps = sdk_capabilities(agent)
    assert caps.contract_version == "sdk" and caps.agent == "sdk"
    assert caps.provides.trajectory and caps.provides.thinking and not caps.provides.tool_calls
    assert caps.provides.token_usage and caps.provides.run_listing
    assert caps.accepts.files and caps.accepts.model_override and not caps.accepts.stop
    assert caps.limits.max_concurrent_runs == 3


def test_traced_capabilities_rewrites_trajectory_flags():
    harness = Capabilities(
        agent="h", provides={"trajectory": False, "tool_calls": False, "workspace": True}
    )
    same = traced_capabilities(harness, fake_agent("h"))
    assert same is harness
    traced_agent = fake_agent(
        "h",
        trajectory_source=TrajectorySource.OTEL,
        settings=AgentSettings(provides={"thinking": True}),
    )
    traced = traced_capabilities(harness, traced_agent)
    assert traced.provides.trajectory and traced.provides.tool_calls
    assert traced.provides.token_usage
    assert traced.provides.workspace and traced.provides.thinking
    assert harness.provides.trajectory is False
