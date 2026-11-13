from datetime import UTC, datetime, timedelta

import pytest

from evalkit.core.traces.mapping import AttributeKeys
from evalkit.core.traces.raw import RawSpan
from evalkit.db.agents import AgentStore
from evalkit.db.spans import AttributeDrop, SpanLink, SpanStore, resolve_sessions
from tests.identity import seed_identity, workspace_ref

NOW = datetime(2026, 10, 9, tzinfo=UTC)


@pytest.fixture
async def db(tmp_path, session_factory):
    factory = session_factory
    await seed_identity(factory, {"admin": "password", "akshat": "password"}, shared=())
    record = await AgentStore(factory, "default").create(
        await workspace_ref(factory, "akshat"), "research-agent"
    )
    return SpanStore(factory, "default"), record.id


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


async def test_list_sessions_marks_a_session_failed_by_its_root_and_spans_its_duration(db):
    store, agent_id = db
    crashed = span("r1", trace="t1", session="crashed", at=0)
    crashed.status_code = "ERROR"
    typed = span("r2", trace="t2", session="typed", at=10, attrs={"error.type": "Timeout"})
    child_error = span("c3", trace="t3", parent="r3", at=21)
    child_error.status_code = "ERROR"
    fine = [span("r3", trace="t3", session="fine", at=20), child_error]
    await store.upsert(agent_id, [crashed, typed, *fine])
    items, _ = await store.list_sessions(agent_id, query=None, limit=10, offset=0)
    by_id = {i.session_id: i for i in items}
    assert {k: v.failed for k, v in by_id.items()} == {
        "crashed": True,
        "typed": True,
        "fine": False,
    }
    assert by_id["fine"].ended_at - by_id["fine"].created_at == timedelta(seconds=2)


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


async def test_spans_under_a_sessionless_root_take_the_outermost_ancestors_session(db):
    store, agent_id = db
    await store.upsert(agent_id, [span("llm", parent="agent", at=3)])
    await store.upsert(agent_id, [span("agent", parent="turn", session="X", at=2)])
    await store.upsert(agent_id, [span("http", name="POST /chat")])
    await store.upsert(agent_id, [span("turn", parent="http", session="S", at=1)])
    await store.upsert(agent_id, [span("tool", parent="agent", at=4)])
    rows = await store.session_spans(agent_id, "S")
    assert sorted(r.span_id for r in rows) == ["agent", "llm", "tool", "turn"]
    items, total = await store.list_sessions(agent_id, query=None, limit=10, offset=0)
    assert total == 1 and items[0].session_id == "S" and items[0].span_count == 4


async def test_parent_cycles_end_and_keep_their_own_session(db):
    store, agent_id = db
    batch = [
        span("self", parent="self", session="A"),
        span("a", parent="b", session="B"),
        span("b", parent="a"),
        span("child", parent="self"),
    ]
    assert await store.upsert(agent_id, batch) == 4
    assert [r.span_id for r in await store.session_spans(agent_id, "A")] == ["self"]
    assert [r.span_id for r in await store.session_spans(agent_id, "B")] == ["a"]


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


def test_resolve_sessions_walks_a_deep_chain_once():
    links = [SpanLink(0, "t", "s0", "http", "S")]
    links += [
        SpanLink(i, "t", f"s{i}", f"s{i - 1}", "X" if i % 2 else None) for i in range(1, 10_000)
    ]
    resolved = resolve_sessions(links)
    assert len(resolved) == 10_000 and set(resolved.values()) == {"S"}


async def received(store, agent_id, session) -> dict[str, datetime]:
    return {r.span_id: r.received_at for r in await store.session_spans(agent_id, session)}


async def test_an_unchanged_span_keeps_its_received_at(db):
    store, agent_id = db
    await store.upsert(agent_id, [span("a", session="S")])
    before = await received(store, agent_id, "S")
    await store.upsert(agent_id, [span("a", session="S")])
    assert await received(store, agent_id, "S") == before


async def test_a_changed_span_moves_its_received_at(db):
    store, agent_id = db
    await store.upsert(agent_id, [span("a", session="S")])
    before = await received(store, agent_id, "S")
    await store.upsert(agent_id, [span("a", session="S", attrs={"v": 2})])
    assert (await received(store, agent_id, "S"))["a"] > before["a"]


async def test_a_session_fix_alone_keeps_received_at(db):
    store, agent_id = db
    await store.upsert(agent_id, [span("a")])
    [stored] = await store.session_spans(agent_id, "t1")
    await store.upsert(agent_id, [span("a", session="S").model_copy(update={"attributes": {}})])
    assert await received(store, agent_id, "S") == {"a": stored.received_at}


async def test_an_empty_session_never_wipes_a_stored_one(db):
    store, agent_id = db
    await store.upsert(agent_id, [span("a", session="S")])
    await store.upsert(agent_id, [span("a", attrs={"v": 2})])
    rows = await store.session_spans(agent_id, "S")
    assert [r.span_id for r in rows] == ["a"] and rows[0].attributes == {"v": 2}


async def test_a_child_with_its_own_session_settles(db):
    store, agent_id = db
    batch = [span("root", session="S"), span("sub", parent="root", session="sub-conv")]
    await store.upsert(agent_id, batch)
    before = await received(store, agent_id, "S")
    await store.upsert(agent_id, batch)
    assert await received(store, agent_id, "S") == before
