import json
from datetime import UTC, datetime

from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.convert import to_atif
from evalkit.core.traces.mapping import MESSAGE_FORMATS, to_esf
from evalkit.core.traces.presets import read_preset
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sources.langfuse import to_raw
from tests.core.traces.test_langfuse_source import OBS, OBSERVE_TRACE, OTEL_TRACE

PRESET = json.loads(read_preset("langfuse"))


def trajectory(trace_id: str, doc: dict):
    mapping = MappingDocument.model_validate(doc)
    esf = [to_esf(to_raw(o), mapping) for o in OBS if o["traceId"] == trace_id]
    assert all(e.unmapped == [] for e in esf)
    return to_atif(esf, mapping, agent_name="a", session_id="s", mapping_version=1)


def test_an_otel_agent_imported_from_langfuse_gives_the_full_trajectory():
    t = trajectory(OTEL_TRACE, PRESET)
    assert [s.source for s in t.steps] == ["system", "user", "agent", "agent"]
    assert t.steps[0].message == "You answer stock questions."
    call = t.steps[2]
    assert [(c.tool_call_id, c.function_name, c.arguments) for c in call.tool_calls or []] == [
        ("call-1", "lookup_price", {"ticker": "AAPL"})
    ]
    assert call.observation is not None
    assert call.observation.results[0].content == '{"price": 101.5}'
    assert call.metrics is not None
    assert (call.metrics.prompt_tokens, call.metrics.cached_tokens) == (1000, 800)
    assert call.metrics.cost_usd == 0.0012525
    assert t.steps[-1].message == "AAPL is 101.5"
    assert t.final_metrics is not None and t.final_metrics.total_prompt_tokens == 2200


def test_an_observe_agent_reads_its_messages_through_a_path():
    plain = trajectory(OBSERVE_TRACE, PRESET)
    assert [s.message for s in plain.steps] == ["AAPL is 101.5"]
    edited = {
        **PRESET,
        "input_messages": {
            "attribute": "langfuse.observation.input",
            "path": "args.0",
            "message_format": "openai",
        },
        "output_messages": {"attribute": "langfuse.observation.output", "message_format": "openai"},
    }
    t = trajectory(OBSERVE_TRACE, edited)
    assert [(s.source, s.message) for s in t.steps] == [
        ("user", "price of AAPL?"),
        ("agent", "AAPL is 101.5"),
    ]
    assert t.steps[-1].metrics is not None
    assert (t.steps[-1].metrics.prompt_tokens, t.steps[-1].metrics.cached_tokens) == (1000, 800)


def test_a_langfuse_sdk_span_at_the_collector_gives_the_same_tokens():
    at_collector = RawSpan(
        trace_id="t" * 32,
        span_id="s" * 16,
        name="llm-call",
        start_time=datetime(2026, 11, 16, tzinfo=UTC),
        attributes={
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "claude-sonnet-4-5",
            "langfuse.observation.usage_details": json.dumps(
                {"input": 200, "output": 10, "cache_read_input_tokens": 800}
            ),
            "langfuse.observation.cost_details": json.dumps({"total": 0.00099}),
            "session.id": "observe-session-1",
        },
    )
    span = to_esf(at_collector, MappingDocument.model_validate(PRESET))
    assert (span.kind, span.prompt_tokens, span.completion_tokens, span.cached_tokens) == (
        "llm",
        1000,
        10,
        800,
    )
    assert span.cost_usd == 0.00099


def make_langchain_span(span_id: str, second: int, attributes: dict) -> RawSpan:
    return RawSpan(
        trace_id="t" * 32,
        span_id=span_id * 16,
        parent_span_id="r" * 16,
        name=attributes["name"],
        start_time=datetime(2026, 11, 16, 0, 0, second, tzinfo=UTC),
        end_time=datetime(2026, 11, 16, 0, 0, second + 1, tzinfo=UTC),
        attributes={
            "session.id": "lc-session",
            **{k: v for k, v in attributes.items() if k != "name"},
        },
    )


def test_langchain_callback_tool_calls_pair_with_tool_spans_without_ids():
    question = {"role": "user", "content": "price of AAPL and MSFT?"}
    calls = [
        {"name": "lookup_price", "args": {"ticker": "AAPL"}, "id": "c1", "type": "tool_call"},
        {"name": "lookup_price", "args": {"ticker": "MSFT"}, "id": "c2", "type": "tool_call"},
    ]
    first = {"role": "assistant", "content": "", "tool_calls": calls}
    results = [
        {"role": "tool", "content": "101.5", "tool_call_id": "c1"},
        {"role": "tool", "content": "402.0", "tool_call_id": "c2"},
    ]
    raws = [
        make_langchain_span(
            "a",
            0,
            {
                "name": "ChatOpenAI",
                "langfuse.observation.type": "generation",
                "langfuse.observation.input": json.dumps([question]),
                "langfuse.observation.output": json.dumps(first),
            },
        ),
        make_langchain_span(
            "b",
            2,
            {
                "name": "lookup_price",
                "langfuse.observation.type": "tool",
                "langfuse.observation.input": "{'ticker': 'AAPL'}",
                "langfuse.observation.output": "101.5",
            },
        ),
        make_langchain_span(
            "c",
            3,
            {
                "name": "lookup_price",
                "langfuse.observation.type": "tool",
                "langfuse.observation.input": "{'ticker': 'MSFT'}",
                "langfuse.observation.output": "402.0",
            },
        ),
        make_langchain_span(
            "d",
            5,
            {
                "name": "ChatOpenAI",
                "langfuse.observation.type": "generation",
                "langfuse.observation.input": json.dumps([question, first, *results]),
                "langfuse.observation.output": json.dumps(
                    {"role": "assistant", "content": "AAPL 101.5, MSFT 402.0"}
                ),
            },
        ),
    ]
    mapping = MappingDocument.model_validate(PRESET)
    t = to_atif(
        [to_esf(r, mapping) for r in raws],
        mapping,
        agent_name="a",
        session_id="s",
        mapping_version=1,
    )
    call = t.steps[1]
    assert [(c.tool_call_id, c.function_name, c.arguments) for c in call.tool_calls or []] == [
        ("c1", "lookup_price", {"ticker": "AAPL"}),
        ("c2", "lookup_price", {"ticker": "MSFT"}),
    ]
    assert call.observation is not None
    assert [(r.source_call_id, r.content) for r in call.observation.results] == [
        ("c1", "101.5"),
        ("c2", "402.0"),
    ]
    assert t.steps[-1].message == "AAPL 101.5, MSFT 402.0"


def test_a_langchain_callback_generation_from_langfuse_sdk_4():
    read_file = {"file_path": "/data/report.md", "limit": 500, "offset": 0}
    tool_def = {"type": "function", "function": {"name": "ls", "parameters": {}}}
    messages = [
        {
            "role": "system",
            "content": [{"type": "text", "text": "You are "}, {"type": "text", "text": "helpful."}],
        },
        {"role": "user", "content": "summarise the report"},
        {
            "role": "assistant",
            "content": [],
            "tool_calls": [
                {"name": "read_file", "args": read_file, "id": "call_1", "type": "tool_call"}
            ],
            "additional_kwargs": {"function_call": {"name": "read_file", "arguments": "{}"}},
        },
        {"role": "tool", "content": "file text", "tool_call_id": "call_1"},
        {"role": "tool", "content": tool_def},
    ]
    output = {
        "role": "assistant",
        "content": [],
        "tool_calls": [
            {
                "name": "read_file",
                "args": {**read_file, "offset": 500},
                "id": "call_2",
                "type": "tool_call",
            }
        ],
    }
    raw = make_langchain_span(
        "a",
        0,
        {
            "name": "ChatGoogleGenerativeAI",
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": "gemini-3.5-flash-lite",
            "langfuse.observation.input": json.dumps(messages),
            "langfuse.observation.output": json.dumps(output),
            "langfuse.observation.usage_details": json.dumps(
                {
                    "input": 8420,
                    "output": 56,
                    "total": 14588,
                    "input_cache_read": 6097,
                    "output_reasoning": 15,
                }
            ),
            "langfuse.observation.cost_details": json.dumps({"total": 0.00288641}),
        },
    )
    span = to_esf(raw, MappingDocument.model_validate(PRESET))
    inputs = span.input_messages or []
    assert [(m.role, m.text) for m in inputs[:2]] == [
        ("system", "You are helpful."),
        ("user", "summarise the report"),
    ]
    assert [(c.id, c.name) for c in inputs[2].tool_calls or []] == [("call_1", "read_file")]
    assert inputs[3].tool_call_id == "call_1"
    out = (span.output_messages or [])[0]
    assert [(c.id, c.name, c.arguments) for c in out.tool_calls or []] == [
        ("call_2", "read_file", {**read_file, "offset": 500})
    ]
    assert out.text is None
    assert (span.prompt_tokens, span.completion_tokens, span.cached_tokens) == (14517, 71, 6097)
    assert span.cost_usd == 0.00288641


def test_gen_ai_messages_without_parts_read_openai_tool_calls():
    message = {
        "role": "assistant",
        "parts": None,
        "content": "",
        "tool_calls": [
            {
                "id": "c1",
                "type": "function",
                "function": {"name": "ls", "arguments": '{"path": "/"}'},
            }
        ],
    }
    result = {"role": "tool", "parts": [], "content": "a.txt", "tool_call_id": 7}
    messages, _ = MESSAGE_FORMATS["gen_ai"]({"m": json.dumps([message, result])}, "m")
    assert [(c.id, c.name, c.arguments) for c in messages[0].tool_calls or []] == [
        ("c1", "ls", {"path": "/"})
    ]
    assert (messages[1].text, messages[1].tool_call_id) == ("a.txt", "7")
    openai, _ = MESSAGE_FORMATS["openai"]({"m": json.dumps([result])}, "m")
    assert openai[0].tool_call_id == "7"
