from datetime import UTC, datetime, timedelta

import pytest

from evalkit.core.traces.mapping import AttributeKeys
from evalkit.core.traces.raw import RawSpan
from evalkit.db.agents import AgentStore
from evalkit.db.spans import AttributeDrop, SpanStore
from tests.identity import seed_identity

NOW = datetime(2026, 10, 9, tzinfo=UTC)


@pytest.fixture
async def db(tmp_path, session_factory):
    factory = session_factory
    await seed_identity(factory, {"admin": "password", "akshat": "password"}, shared=())
    record = await AgentStore(factory).create("akshat", "research-agent")
    return SpanStore(factory), record.id


def span(span_id, *, trace="t1", parent=None, session=None, at=0, name="s", attrs=None) -> RawSpan:
    attributes = dict(attrs or {})
    if session:
        attributes["session.id"] = session
    return RawSpan(
        trace_id=trace,
        span_id=span_id,
        parent_span_id=parent,
        name=name,
        start_time=NOW + timedelta(seconds=at),
        end_time=NOW + timedelta(seconds=at + 1),
        attributes=attributes,
        session_id_hint=session,
    )


async def test_upsert_resolves_children_to_the_root_session_in_and_out_of_order(db):
    store, agent_id = db
    batch = [span("root", session="S1", name="evalkit.run"), span("child", parent="root")]
    assert await store.upsert(agent_id, batch) == 2
    rows = await store.session_spans(agent_id, "S1")
    assert sorted(r.span_id for r in rows) == ["child", "root"]
    assert rows[0].received_at is not None and rows[0].received_at.tzinfo is not None
    assert await store.upsert(agent_id, [span("late", trace="t2", parent="root2", at=5)]) == 1
    assert await store.upsert(agent_id, [span("root2", trace="t2", session="S2", at=4)]) == 1
    assert sorted(r.span_id for r in await store.session_spans(agent_id, "S2")) == ["late", "root2"]


async def test_resend_is_an_upsert(db):
    store, agent_id = db
    await store.upsert(agent_id, [span("a", session="S", attrs={"v": 1})])
    await store.upsert(agent_id, [span("a", session="S", attrs={"v": 2})])
    rows = await store.session_spans(agent_id, "S")
    assert len(rows) == 1 and rows[0].attributes["v"] == 2


async def test_one_batch_with_many_traces_and_a_repeated_span(db, monkeypatch):
    store, agent_id = db
    monkeypatch.setattr("evalkit.db.spans.UPSERT_CHUNK", 2)
    await store.upsert(agent_id, [span("r3", trace="t3", session="S3")])
    batch = [
        span("r1", trace="t1", session="S1"),
        span("c1", trace="t1", parent="r1"),
        span("c3", trace="t3", parent="r3"),
        span("c1", trace="t1", parent="r1", name="again"),
        span("r2", trace="t2", session="S2"),
    ]
    assert await store.upsert(agent_id, batch) == 5
    one = {r.span_id: r.name for r in await store.session_spans(agent_id, "S1")}
    assert one == {"r1": "s", "c1": "again"}
    assert sorted(r.span_id for r in await store.session_spans(agent_id, "S3")) == ["c3", "r3"]
    assert [r.span_id for r in await store.session_spans(agent_id, "S2")] == ["r2"]


async def test_list_sessions_newest_first_with_prefix_query_and_title(db):
    store, agent_id = db
    first = [
        span("r1", trace="t1", session="aaa-1", name="first", at=0),
        span("c1", trace="t1", parent="r1", at=1),
    ]
    await store.upsert(agent_id, first)
    await store.upsert(agent_id, [span("r2", trace="t2", session="bbb-2", name="second", at=10)])
    await store.upsert(agent_id, [span("orphan", trace="t3", at=20)])
    items, total = await store.list_sessions(agent_id, query=None, limit=10, offset=0)
    assert total == 2 and [i.session_id for i in items] == ["bbb-2", "aaa-1"]
    assert items[1].title == "first" and items[1].span_count == 2
    items, total = await store.list_sessions(agent_id, query="aaa", limit=10, offset=0)
    assert total == 1 and items[0].session_id == "aaa-1"
    items, total = await store.list_unsessioned(agent_id, limit=10, offset=0)
    assert total == 1 and items[0].trace_id == "t3" and items[0].session_id is None


async def test_session_spans_falls_back_to_the_trace_id(db):
    store, agent_id = db
    await store.upsert(
        agent_id,
        [span("orphan", trace="t9", at=1), span("kid", trace="t9", parent="orphan", at=2)],
    )
    rows = await store.session_spans(agent_id, "t9")
    assert sorted(r.span_id for r in rows) == ["kid", "orphan"]


async def test_a_roots_session_wins_over_child_session_attributes(db):
    store, agent_id = db
    early = span("agent-1", parent="root", session="F", at=1)
    await store.upsert(agent_id, [early])
    await store.upsert(agent_id, [span("root", session="S", name="evalkit.run")])
    await store.upsert(agent_id, [span("agent-2", parent="agent-1", session="F", at=2)])
    rows = await store.session_spans(agent_id, "S")
    assert sorted(r.span_id for r in rows) == ["agent-1", "agent-2", "root"]
    items, total = await store.list_sessions(agent_id, query=None, limit=10, offset=0)
    assert total == 1 and items[0].session_id == "S" and items[0].span_count == 3


async def test_children_keep_their_session_when_the_root_has_none(db):
    store, agent_id = db
    batch = [span("root", name="request"), span("turn", parent="root", session="F", at=1)]
    await store.upsert(agent_id, batch)
    items, total = await store.list_sessions(agent_id, query=None, limit=10, offset=0)
    assert total == 1 and items[0].session_id == "F"


async def test_session_read_drops_keys_on_listed_rows_only(db):
    store, agent_id = db
    big = {
        "input.value": "x" * 5000,
        "llm.input_messages.0.message.content": "y" * 3000,
        "llm.input_messagesX": "kept",
        "flag": True,
        "nested": {"a": [1, {"b": False}]},
    }
    batch = [span("root", session="S", attrs=big), span("child", parent="root", at=1, attrs=big)]
    batch[1].events = [{"name": "system prompt", "attributes": {"text": "y" * 100}}]
    batch[1].resource = {"service.name": "support-bot"}
    await store.upsert(agent_id, batch)
    full = await store.session_spans(agent_id, "S")
    assert full[1].events == batch[1].events and full[1].resource == {"service.name": "support-bot"}
    keys = AttributeKeys(frozenset({"input.value"}), frozenset({"llm.input_messages."}))
    async with store.read_session(agent_id, "S") as rows:
        probes = await rows.probes({"flag", "missing"})
        assert [p.span_id for _, p in probes] == ["root", "child"]
        assert probes[0][1].attributes == {"flag": True}
        child = probes[1][0]
        everything = await rows.attribute_bytes(AttributeDrop([], keys))
        assert 16000 < everything < 17000
        assert await rows.attribute_bytes(AttributeDrop([child], keys)) < everything - 8000
        streamed = [s async for s in rows.stream(AttributeDrop([child], keys))]
    assert [s.span_id for s in streamed] == ["root", "child"]
    assert streamed[0].attributes == full[0].attributes
    assert streamed[1].attributes == {
        "llm.input_messagesX": "kept",
        "flag": True,
        "nested": {"a": [1, {"b": False}]},
    }
    assert streamed[1].events == [] and streamed[1].resource == {}
    async with store.read_session(agent_id, "nope") as rows:
        assert await rows.attribute_bytes(AttributeDrop([], keys)) == 0
