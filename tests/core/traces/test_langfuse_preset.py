import json
from datetime import UTC, datetime

from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.convert import to_atif
from evalkit.core.traces.mapping import to_esf
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
