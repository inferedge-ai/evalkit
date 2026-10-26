from evalkit.contracts.enums import TrajectorySource
from evalkit.contracts.settings import AgentSettings
from evalkit.core.agents import AgentDefinition, Driver, driver, server_may_drive


def agent(harness_url, shared):
    return AgentDefinition(
        id=1,
        name="a",
        workspace="dev" if shared else "akshat",
        workspace_is_shared=shared,
        harness_url=harness_url,
        trajectory_source=TrajectorySource.ATIF,
        repo_url=None,
        settings=AgentSettings(),
    )


def test_driver():
    assert driver(agent(None, True)) is Driver.SDK
    assert driver(agent(None, False)) is Driver.SDK
    assert driver(agent("http://h", True)) is Driver.SERVER
    assert driver(agent("http://h", False)) is Driver.CLI


def test_server_may_drive_only_shared_harness_agents():
    assert server_may_drive(agent("http://h", True))
    assert not server_may_drive(agent("http://h", False))
    assert not server_may_drive(agent(None, True))
