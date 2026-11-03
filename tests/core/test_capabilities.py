import pytest
from pydantic import ValidationError

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
    assert caps.input_schema is None and caps.effective_input_schema["required"] == ["question"]


def test_sdk_snapshot_carries_the_agent_input_schema():
    schema = {"type": "object", "required": ["turns"]}
    agent = fake_agent("sdk", harness_url=None, settings=AgentSettings(input_schema=schema))
    assert sdk_capabilities(agent).effective_input_schema == schema


def test_agent_settings_refuse_an_invalid_input_schema():
    with pytest.raises(ValidationError, match="not a valid JSON Schema"):
        AgentSettings(input_schema={"type": 5})


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
