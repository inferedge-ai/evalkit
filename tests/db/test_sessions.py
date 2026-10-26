import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from evalkit.config import OtelConfig
from evalkit.contracts.enums import TrajectorySource
from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sessions import SessionTooLarge, build_session_trajectory
from evalkit.db.agents import AgentStore
from evalkit.db.mappings import MappingStore
from evalkit.db.sessions import DbSessionReader
from evalkit.db.spans import SpanStore
from tests.fake_agents import REFERENCE, reference_mapping
from tests.identity import seed_identity

START = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
SESSION = "chat-1"


class Clock:
    def __init__(self) -> None:
        self.at = START

    def tick(self) -> datetime:
        self.at += timedelta(milliseconds=250)
        return self.at


def _span(
    clock: Clock, trace: str, span_id: str, parent: str | None, name: str, attributes: dict
) -> RawSpan:
    started = clock.tick()
    return RawSpan(
        trace_id=trace,
        span_id=span_id,
        parent_span_id=parent,
        name=name,
        start_time=started,
        end_time=started + timedelta(milliseconds=100),
        attributes=attributes,
        session_id_hint=SESSION if parent is None else None,
    )


def _openinference_llm(context: list[tuple[str, str]], answer: str, call: str | None) -> dict:
    attributes: dict = {
        "openinference.span.kind": "LLM",
        "llm.model_name": "m-oi",
        "llm.token_count.prompt": 100 + len(context),
        "llm.token_count.completion": 7,
        "llm.token_count.prompt_details.cache_read": 50,
        "input.value": json.dumps([{"role": r, "content": t} for r, t in context]),
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.contents.0.message_content.type": "reasoning",
        "llm.output_messages.0.message.contents.0.message_content.text": f"think {answer}",
        "llm.output_messages.0.message.content": answer,
        "llm.invocation_parameters": '{"temperature": 0}',
    }
    for j, (role, text) in enumerate(context):
        attributes[f"llm.input_messages.{j}.message.role"] = role
        attributes[f"llm.input_messages.{j}.message.content"] = text
    if call:
        prefix = "llm.output_messages.0.message.tool_calls.0.tool_call."
        attributes[prefix + "id"] = call
        attributes[prefix + "function.name"] = "task" if call.startswith("task") else "search"
        attributes[prefix + "function.arguments"] = json.dumps({"q": call})
    return attributes


def _openinference_agent(
    clock: Clock, trace: str, agent: str, parent: str | None, turns: int, chunk: int, task: bool
) -> Iterator[RawSpan]:
    yield _span(
        clock,
        trace,
        agent,
        parent,
        agent,
        {"openinference.span.kind": "AGENT", "agent.name": agent, "input.value": "go"},
    )
    context = [("system", f"{agent} rules " + "s" * chunk), ("user", f"{agent} question")]
    for i in range(turns):
        last = i == turns - 1
        call = None if last else (f"task-{agent}" if task and i == 1 else f"{agent}-call-{i}")
        llm = _openinference_llm(context, f"{agent} step {i}", call)
        yield _span(clock, trace, f"{agent}-llm-{i}", agent, "llm", llm)
        if call is None:
            return
        tool = f"{agent}-tool-{i}"
        result = f"{agent} result {i} " + "r" * chunk
        tool_attributes = {
            "openinference.span.kind": "TOOL",
            "tool.name": "task" if call.startswith("task") else "search",
            "tool.id": call,
            "input.value": json.dumps({"q": call}),
            "output.value": result,
        }
        yield _span(clock, trace, tool, agent, "tool", tool_attributes)
        if call.startswith("task"):
            yield from _openinference_agent(
                clock, trace, f"{agent}-sub", tool, turns=3, chunk=chunk, task=False
            )
        context += [("assistant", f"{agent} step {i}"), ("tool", result)]


def realistic_session(turns: int = 6, chunk: int = 300) -> list[RawSpan]:
    clock = Clock()
    return list(_openinference_agent(clock, "a" * 32, "main", None, turns, chunk, task=True))


@pytest.fixture
async def stores(tmp_path, session_factory):
    factory = session_factory
    await seed_identity(factory, {"admin": "password", "akshat": "password"}, shared=())
    agents = AgentStore(factory)
    record = await agents.create(
        "akshat",
        "support-bot",
        trajectory_source=TrajectorySource.OTEL,
        mapping=reference_mapping(),
    )
    spans, mappings = SpanStore(factory), MappingStore(factory)
    reader = DbSessionReader(agents, spans, mappings, OtelConfig())
    return reader, spans, mappings, record.id


async def _full_build(
    spans: SpanStore, agent_id: int, document: MappingDocument | None, version: int | None = None
):
    stored = await spans.session_spans(agent_id, SESSION)
    return build_session_trajectory(
        stored,
        agent_name="support-bot",
        active_mapping=document,
        mapping_version=version,
        now=datetime.now(UTC),
        settle_seconds=10,
        include_esf=True,
    )


def _llm_inputs(result) -> dict[str, int]:
    return {s.span_id: len(s.input_messages or []) for s in result.esf if s.kind == "llm"}


async def test_trajectory_is_unchanged_and_only_first_llm_spans_keep_their_input(stores):
    reader, spans, _, agent_id = stores
    await spans.upsert(agent_id, realistic_session())
    full = await _full_build(spans, agent_id, REFERENCE["openinference"], 1)
    read = await reader.read("support-bot@akshat", SESSION, include_esf=True)
    assert read is not None and full.trajectory is not None
    assert json.dumps(read.trajectory) == json.dumps(full.trajectory)
    assert len(full.trajectory["subagent_trajectories"]) == 1
    firsts = {"main-llm-0", "main-sub-llm-0"}
    inputs = _llm_inputs(read)
    assert {k for k, n in inputs.items() if n} == firsts
    assert all(_llm_inputs(full)[k] == inputs[k] for k in firsts)
    assert [s.output_messages for s in read.esf] == [s.output_messages for s in full.esf]
    assert read.unmapped_attributes == full.unmapped_attributes
    raw = [s async for s in spans.stream_session_spans(agent_id, SESSION)]
    assert raw == await spans.session_spans(agent_id, SESSION)
    later = next(s for s in raw if s.span_id == "main-llm-3")
    assert "llm.input_messages.0.message.content" in later.attributes


async def test_size_cap_counts_only_the_attributes_a_build_loads(stores):
    reader, spans, _, agent_id = stores
    session = realistic_session(turns=8, chunk=2000)
    await spans.upsert(agent_id, session)
    stored = sum(len(json.dumps(s.attributes, ensure_ascii=False)) for s in session)
    async with spans.read_session(agent_id, SESSION) as rows:
        probes = await rows.probes({"openinference.span.kind", "gen_ai.operation.name"})
        assert len(probes) == len(session)
        assert probes[1][1].attributes == {"openinference.span.kind": "LLM"}
    reader._otel.max_session_bytes = stored // 2
    result = await reader.read("support-bot@akshat", SESSION)
    assert result is not None and result.trajectory is not None
    reader._otel.max_session_bytes = stored // 8
    with pytest.raises(SessionTooLarge, match="otel.max_session_bytes"):
        await reader.read("support-bot@akshat", SESSION)


async def test_a_draft_mapping_decides_which_keys_are_input_context(stores):
    reader, spans, mappings, agent_id = stores
    document = {
        "schema_version": "1",
        "kind": {"attribute": "x.kind", "values": {"model": "llm", "fn": "tool", "run": "agent"}},
        "system_instructions": {"attribute": "x.system"},
        "input_messages": {"attribute": "x.prompt", "message_format": "openai"},
        "output_messages": {"attribute": "x.reply", "message_format": "openai"},
        "model": {"attribute": "x.model"},
        "tool": {"name": {"attribute": "x.tool"}, "call_id": {"attribute": "x.call"}},
        "input": {"attribute": "x.args", "json": True},
        "output": {"attribute": "x.result"},
    }
    clock = Clock()
    trace = "c" * 32
    session = [_span(clock, trace, "root", None, "run", {"x.kind": "run", "x.args": "hi"})]
    history: list[dict] = [{"role": "user", "content": "hi"}]
    for i in range(3):
        reply = [{"role": "assistant", "content": f"r{i}", "tool_calls": []}]
        if i < 2:
            reply[0]["tool_calls"] = [{"id": f"c{i}", "function": {"name": "f", "arguments": "{}"}}]
        attributes = {
            "x.kind": "model",
            "x.model": "m",
            "x.system": "be brief",
            "x.prompt": json.dumps(history),
            "x.reply": json.dumps(reply),
            "x.args": "ignored on llm spans",
            "x.extra": "kept",
        }
        session.append(_span(clock, trace, f"llm-{i}", "root", "model", attributes))
        if i < 2:
            tool = {"x.kind": "fn", "x.tool": "f", "x.call": f"c{i}", "x.result": f"out{i}"}
            session.append(_span(clock, trace, f"tool-{i}", "root", "fn", tool))
            history += [*reply, {"role": "tool", "content": f"out{i}", "tool_call_id": f"c{i}"}]
    await spans.upsert(agent_id, session)
    await mappings.put_draft(agent_id, document, "akshat")
    full = await _full_build(spans, agent_id, MappingDocument.model_validate(document), 2)
    read = await reader.read("support-bot@akshat", SESSION, mapping="draft", include_esf=True)
    assert read is not None and read.trajectory == full.trajectory
    assert _llm_inputs(read) == {"llm-0": 2, "llm-1": 0, "llm-2": 0}
    assert _llm_inputs(full) == {"llm-0": 2, "llm-1": 4, "llm-2": 6}
    later = next(s for s in read.esf if s.span_id == "llm-2")
    assert later.unmapped == ["x.extra"] and later.model == "m"
    active = await reader.read("support-bot@akshat", SESSION, include_esf=True)
    assert active is not None and {s.kind for s in active.esf} == {"other"}
