import pytest

from evalkit.contracts.enums import TrajectorySource
from evalkit.contracts.settings import AgentSettings
from evalkit.core.agents import UnknownAgent
from evalkit.db.agents import AgentExists, AgentStore, InvalidAgent
from evalkit.db.mappings import MappingStore
from tests.fake_agents import reference_mapping
from tests.identity import seed_identity


@pytest.fixture
async def store(session_factory):
    await seed_identity(
        session_factory, {"admin": "password", "akshat": "password"}, shared=("dev",)
    )
    return AgentStore(session_factory)


async def test_create_get_and_list_with_shared_first(store):
    await store.create("akshat", "myagent", harness_url="http://localhost:9000")
    await store.create("dev", "ouragent", harness_url="http://ouragent")
    mine = await store.get("myagent@akshat")
    assert mine.workspace == "akshat" and not mine.workspace_is_shared
    assert (await store.get("ouragent@dev")).workspace_is_shared
    assert [a.ref for a in await store.list_agents()] == ["ouragent@dev", "myagent@akshat"]
    assert [a.ref for a in await store.list_agents(workspace="akshat")] == ["myagent@akshat"]
    with pytest.raises(UnknownAgent, match="create it with evalkit agents create"):
        await store.get("ghost@dev")


async def test_duplicate_name_in_one_workspace(store):
    await store.create("dev", "ouragent")
    with pytest.raises(AgentExists):
        await store.create("dev", "ouragent")
    await store.create("akshat", "ouragent")


async def test_otel_agent_needs_a_mapping_and_gets_v1_active(store, session_factory):
    with pytest.raises(InvalidAgent, match="need a mapping"):
        await store.create("dev", "traced", trajectory_source=TrajectorySource.OTEL)
    agent = await store.create(
        "dev", "traced", trajectory_source=TrajectorySource.OTEL, mapping=reference_mapping()
    )
    active = await MappingStore(session_factory).active(agent.id)
    assert active is not None and active.version == 1


async def test_archive_hides_and_edit_keeps_trajectory_source(store):
    await store.create("dev", "ouragent", harness_url="http://a")
    edited = await store.edit(
        "ouragent@dev", harness_url="http://b", settings=AgentSettings(max_concurrent_runs=9)
    )
    assert edited.harness_url == "http://b" and edited.settings.max_concurrent_runs == 9
    assert edited.trajectory_source is TrajectorySource.ATIF
    await store.archive("ouragent@dev")
    assert await store.list_agents() == []
    assert (await store.get("ouragent@dev")).archived


async def test_edit_checks_provides_on_the_final_row(store):
    provides = AgentSettings(provides={"thinking": True})
    await store.create("dev", "coded", settings=provides)
    with pytest.raises(InvalidAgent, match="provides"):
        await store.edit("coded@dev", harness_url="http://h")
    assert (await store.get("coded@dev")).harness_url is None
