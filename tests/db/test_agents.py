import pytest

from evalkit.contracts.enums import TrajectorySource
from evalkit.contracts.settings import AgentSettings
from evalkit.core.agents import UnknownAgent
from evalkit.db.agents import AgentExists, AgentStore, InvalidAgent
from evalkit.db.mappings import MappingStore
from evalkit.db.models import Tenant
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_agents import reference_mapping
from tests.identity import seed_identity, workspace_ref


@pytest.fixture
async def store(session_factory):
    await seed_identity(
        session_factory, {"admin": "password", "akshat": "password"}, shared=("dev",)
    )
    return AgentStore(session_factory, 1)


async def test_create_get_and_list_with_shared_first(store, session_factory):
    await store.create(
        await workspace_ref(session_factory, "akshat"),
        "myagent",
        harness_url="http://localhost:9000",
    )
    await store.create(
        await workspace_ref(session_factory, "dev"), "ouragent", harness_url="http://ouragent"
    )
    mine = await store.get("myagent@akshat")
    assert mine.workspace == "akshat" and not mine.workspace_is_shared
    assert (await store.get("ouragent@dev")).workspace_is_shared
    assert [a.ref for a in await store.list_agents()] == ["ouragent@dev", "myagent@akshat"]
    assert [a.ref for a in await store.list_agents(workspace="akshat")] == ["myagent@akshat"]
    with pytest.raises(UnknownAgent, match="create it with evalkit agents create"):
        await store.get("ghost@dev")


async def test_duplicate_name_in_one_workspace(store, session_factory):
    await store.create(await workspace_ref(session_factory, "dev"), "ouragent")
    with pytest.raises(AgentExists):
        await store.create(await workspace_ref(session_factory, "dev"), "ouragent")
    await store.create(await workspace_ref(session_factory, "akshat"), "ouragent")


async def test_otel_agent_needs_a_mapping_and_gets_v1_active(store, session_factory):
    with pytest.raises(InvalidAgent, match="need a mapping"):
        await store.create(
            await workspace_ref(session_factory, "dev"),
            "traced",
            trajectory_source=TrajectorySource.OTEL,
        )
    agent = await store.create(
        await workspace_ref(session_factory, "dev"),
        "traced",
        trajectory_source=TrajectorySource.OTEL,
        mapping=reference_mapping(),
    )
    active = await MappingStore(session_factory, 1).active(agent.id)
    assert active is not None and active.version == 1


async def test_archive_hides_and_edit_keeps_trajectory_source(store, session_factory):
    await store.create(
        await workspace_ref(session_factory, "dev"), "ouragent", harness_url="http://a"
    )
    edited = await store.edit(
        "ouragent@dev", harness_url="http://b", settings=AgentSettings(max_concurrent_runs=9)
    )
    assert edited.harness_url == "http://b" and edited.settings.max_concurrent_runs == 9
    assert edited.trajectory_source is TrajectorySource.ATIF
    await store.archive("ouragent@dev")
    assert await store.list_agents() == []
    assert (await store.get("ouragent@dev")).archived


async def test_edit_checks_provides_on_the_final_row(store, session_factory):
    provides = AgentSettings(provides={"thinking": True})
    await store.create(await workspace_ref(session_factory, "dev"), "coded", settings=provides)
    with pytest.raises(InvalidAgent, match="provides"):
        await store.edit("coded@dev", harness_url="http://h")
    assert (await store.get("coded@dev")).harness_url is None


async def test_agent_lookups_stay_inside_the_tenant(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        await s.commit()
    for tenant_id in (1, 2):
        admin = await UserStore(raw_session_factory, tenant_id).create(
            "admin", "password", is_admin=True
        )
        dev = await WorkspaceStore(raw_session_factory, tenant_id).create_shared("dev", admin.id)
        await AgentStore(raw_session_factory, tenant_id).create(dev, "bot", harness_url="https://h")
    ours = await AgentStore(raw_session_factory, 1).get("bot@dev")
    theirs = await AgentStore(raw_session_factory, 2).get("bot@dev")
    assert ours.id != theirs.id
    assert [a.id for a in await AgentStore(raw_session_factory, 1).list_agents()] == [ours.id]
    with pytest.raises(UnknownAgent):
        await AgentStore(raw_session_factory, 1).get_by_id(theirs.id)
