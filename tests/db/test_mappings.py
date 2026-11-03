import pytest

from evalkit.db.agents import AgentStore
from evalkit.db.mappings import MappingStore
from tests.identity import seed_identity

DOC = {"schema_version": "1", "kind": {"attribute": "k"}}


@pytest.fixture
async def db(tmp_path, session_factory):
    factory = session_factory
    await seed_identity(factory, {"admin": "password"}, shared=("w",))
    record = await AgentStore(factory).create("w", "a")
    return MappingStore(factory), record.id


async def test_draft_replace_activate_lifecycle(db):
    store, agent_id = db
    assert await store.active(agent_id) is None
    first, _ = await store.put_draft(agent_id, DOC, "akshat")
    assert first.version == 1 and first.status == "draft"
    edited = {**DOC, "model": {"attribute": "m"}}
    second, unchanged = await store.put_draft(agent_id, edited, "akshat")
    assert second.version == 2 and second.status == "draft" and not unchanged
    same, unchanged = await store.put_draft(agent_id, edited, "x")
    assert same.version == 2 and unchanged
    versions = {m.version: m.status for m in await store.list(agent_id)}
    assert versions == {1: "retired", 2: "draft"}
    active, replaced = await store.activate(agent_id, 2)
    assert active.status == "active" and active.activated_at is not None and replaced is None
    assert (await store.draft(agent_id)) is None
    third, _ = await store.put_draft(agent_id, DOC, "akshat")
    _, replaced = await store.activate(agent_id, third.version)
    assert replaced == 2
    versions = {m.version: m.status for m in await store.list(agent_id)}
    assert versions == {1: "retired", 2: "retired", 3: "active"}
    with pytest.raises(KeyError):
        await store.activate(agent_id, 99)
