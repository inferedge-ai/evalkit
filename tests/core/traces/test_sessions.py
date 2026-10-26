import json
import tracemalloc
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.convert import to_atif
from evalkit.core.traces.mapping import mapping_for, to_esf
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sessions import agent_spans, build_session_trajectory
from evalkit.core.traces.settle import root_present, settled
from tests.fake_agents import REFERENCE

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def raw(span_id, *, parent=None, received_ago=30, attributes=None, name="s") -> RawSpan:
    return RawSpan(
        trace_id="t" * 32,
        span_id=span_id,
        parent_span_id=parent,
        name=name,
        start_time=NOW - timedelta(seconds=60),
        end_time=NOW - timedelta(seconds=50),
        attributes=attributes or {},
        received_at=NOW - timedelta(seconds=received_ago),
    )


def test_settled_and_root_present():
    assert settled([raw("a", received_ago=11)], NOW, 10) is True
    assert (
        settled([raw("a", received_ago=11), raw("b", parent="a", received_ago=3)], NOW, 10) is False
    )
    assert settled([], NOW, 10) is False
    assert root_present([raw("a")]) is True and root_present([raw("b", parent="a")]) is False
    assert root_present([]) is False


def test_build_session_trajectory_with_sdk_root_under_custom_mapping():
    custom = MappingDocument.model_validate(
        {
            "schema_version": "1",
            "kind": {"attribute": "my.kind", "values": {"L": "llm"}},
            "output_messages": {"attribute": "out", "message_format": "openai"},
        }
    )
    spans = [
        raw(
            "root",
            name="evalkit.run",
            attributes={
                "evalkit.sdk_root": True,
                "openinference.span.kind": "CHAIN",
                "session.id": "r1",
                "output.value": "42",
            },
        ),
        raw(
            "llm",
            parent="root",
            attributes={
                "my.kind": "L",
                "out": '[{"role": "assistant", "content": "42"}]',
                "extra.attr": 1,
            },
        ),
    ]
    result = build_session_trajectory(
        spans,
        agent_name="a",
        active_mapping=custom,
        mapping_version=2,
        now=NOW,
        settle_seconds=10,
        include_esf=True,
    )
    assert result.settled and result.root_present and result.span_count == 2
    assert result.mapping_version == 2
    assert result.unmapped_attributes == ["extra.attr"]
    assert result.trajectory["steps"][-1]["message"] == "42"
    assert result.trajectory["session_id"] == "r1"
    assert [s.kind for s in result.esf] == ["chain", "llm"]


def test_session_id_follows_the_mapping_rules():
    spans = [
        raw(
            "root", attributes={"gen_ai.conversation.id": "conv-7", "gen_ai.operation.name": "chat"}
        )
    ]
    result = build_session_trajectory(
        spans,
        agent_name="a",
        active_mapping=REFERENCE["gen_ai"],
        mapping_version=None,
        now=NOW,
        settle_seconds=10,
    )
    assert result.trajectory["session_id"] == "conv-7"


def test_empty_session():
    result = build_session_trajectory(
        [],
        agent_name="a",
        active_mapping=None,
        mapping_version=None,
        now=NOW,
        settle_seconds=10,
    )
    assert (result.settled, result.root_present, result.span_count, result.trajectory) == (
        False,
        False,
        0,
        None,
    )


def linked_root(trace: str) -> RawSpan:
    return raw("sdk", name="evalkit.run", attributes=LINKED).model_copy(update={"trace_id": trace})


LINKED = {"evalkit.sdk_root": True, "evalkit.task_session": True, "session.id": "own"}


def test_agent_spans_drops_a_linked_sdk_trace_once_the_agents_trace_is_in():
    agent = [
        raw("turn", name="chat_turn", attributes={"session.id": "own"}),
        raw("llm", parent="turn"),
    ]
    assert agent_spans([linked_root("a" * 32), *agent]) == agent
    assert agent_spans([linked_root("a" * 32)]) == []
    continued = [raw("sdk", name="evalkit.run", attributes=LINKED), raw("turn", parent="sdk")]
    assert agent_spans(continued) == continued
    unlinked = [raw("sdk", attributes={"evalkit.sdk_root": True}), *agent]
    assert agent_spans(unlinked) == unlinked


def _copy(text: str) -> str:
    return text[:-1] + text[-1]


def _llm_attributes(context: list[tuple[str, str]], answer: str, call_id: str | None) -> dict:
    attributes: dict = {
        "openinference.span.kind": "LLM",
        "llm.model_name": "m",
        "llm.token_count.prompt": 10,
        "input.value": json.dumps([{"role": r, "content": t} for r, t in context]),
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.content": answer,
    }
    for j, (role, text) in enumerate(context):
        attributes[f"llm.input_messages.{j}.message.role"] = role
        attributes[f"llm.input_messages.{j}.message.content"] = _copy(text)
    if call_id:
        prefix = "llm.output_messages.0.message.tool_calls.0.tool_call."
        attributes[prefix + "id"] = call_id
        attributes[prefix + "function.name"] = "search"
        attributes[prefix + "function.arguments"] = '{"q": "x"}'
    return attributes


def chat_session(turns: int, chunk: int) -> Iterator[RawSpan]:
    context = [("system", "s" * chunk), ("user", "Summarise Q2")]
    yield raw("root", name="chat", attributes={"openinference.span.kind": "AGENT"})
    for i in range(turns):
        last = i == turns - 1
        call_id = None if last else f"call-{i}"
        at = NOW - timedelta(seconds=59 - i)
        llm = raw(f"llm-{i}", parent="root", name="llm")
        yield llm.model_copy(
            update={
                "start_time": at,
                "attributes": _llm_attributes(context, f"step {i}", call_id),
            }
        )
        if last:
            return
        result = f"{i}:" + "r" * chunk
        tool_attributes = {
            "openinference.span.kind": "TOOL",
            "tool.name": "search",
            "tool.id": call_id,
            "output.value": result,
        }
        yield raw(f"tool-{i}", parent="root", name="search").model_copy(
            update={"start_time": at + timedelta(milliseconds=500), "attributes": tool_attributes}
        )
        context += [("assistant", f"step {i}"), ("tool", result)]


def test_streamed_build_matches_building_from_every_span_at_once():
    spans = list(chat_session(turns=6, chunk=50))
    spans.append(raw("open", parent="root", name="late").model_copy(update={"end_time": None}))
    result = build_session_trajectory(
        spans,
        agent_name="support-bot",
        active_mapping=REFERENCE["openinference"],
        mapping_version=None,
        now=NOW,
        settle_seconds=10,
        include_esf=True,
    )
    esf = [
        to_esf(s, mapping_for(s.attributes, REFERENCE["openinference"])) for s in agent_spans(spans)
    ]
    expected = to_atif(
        esf,
        REFERENCE["openinference"],
        agent_name="support-bot",
        session_id=None,
        mapping_version=None,
        incomplete_ended_at=max(s.received_at for s in spans if s.received_at),
    )
    assert result.trajectory == expected.model_dump(exclude_none=True)
    assert result.esf == esf
    assert result.unmapped_attributes == ["input.value"]
    llms = [s for s in result.esf if s.kind == "llm"]
    assert llms[1].input_messages[0].text is llms[4].input_messages[0].text


def test_build_keeps_repeated_context_once():
    turns, chunk = 40, 20_000
    total = sum(len(json.dumps(s.attributes)) for s in chat_session(turns=turns, chunk=chunk))
    tracemalloc.start()
    build_session_trajectory(
        chat_session(turns=turns, chunk=chunk),
        agent_name="support-bot",
        active_mapping=REFERENCE["openinference"],
        mapping_version=None,
        now=NOW,
        settle_seconds=10,
        include_esf=True,
    )
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert total > 30_000_000
    assert peak < total / 4
