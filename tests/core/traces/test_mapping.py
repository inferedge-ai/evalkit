import json
from datetime import UTC, datetime

from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.mapping import (
    SDK_ROOT_MAPPING,
    AttributeKeys,
    input_context,
    kind_attributes,
    mapping_for,
    session_id_of,
    to_esf,
)
from evalkit.core.traces.raw import RawSpan
from tests.fake_agents import REFERENCE

NOW = datetime(2026, 10, 9, tzinfo=UTC)


def raw(attributes: dict, **overrides) -> RawSpan:
    base = dict(
        trace_id="t" * 32,
        span_id="s" * 16,
        parent_span_id=None,
        name="span",
        kind="INTERNAL",
        start_time=NOW,
        end_time=NOW,
        status_code="UNSET",
        status_message=None,
        attributes=attributes,
        events=[],
        resource={},
        received_at=NOW,
    )
    return RawSpan(**{**base, **overrides})


OPENINFERENCE_LLM = {
    "openinference.span.kind": "LLM",
    "llm.model_name": "claude-sonnet-4-5",
    "llm.input_messages.0.message.role": "system",
    "llm.input_messages.0.message.content": "Be brief.",
    "llm.input_messages.1.message.role": "user",
    "llm.input_messages.1.message.content": "Revenue?",
    "llm.output_messages.0.message.role": "assistant",
    "llm.output_messages.0.message.content": "",
    "llm.output_messages.0.message.tool_calls.0.tool_call.id": "call_1",
    "llm.output_messages.0.message.tool_calls.0.tool_call.function.name": "lookup",
    "llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments": '{"q": "rev"}',
    "llm.token_count.prompt": 10,
    "llm.token_count.completion": 4,
    "llm.token_count.prompt_details.cache_read": 2,
    "llm.prompt_template.version": "v3",
}


def test_openinference_llm_span():
    span = to_esf(raw(OPENINFERENCE_LLM), REFERENCE["openinference"])
    assert span.kind == "llm" and span.model == "claude-sonnet-4-5"
    assert [m.role for m in span.input_messages] == ["system", "user"]
    call = span.output_messages[0].tool_calls[0]
    assert call.id == "call_1" and call.name == "lookup" and call.arguments == {"q": "rev"}
    assert (span.prompt_tokens, span.completion_tokens, span.cached_tokens) == (10, 4, 2)
    assert span.unmapped == ["llm.prompt_template.version"]


def test_openinference_tool_span_reads_tool_rules_and_json():
    span = to_esf(
        raw(
            {
                "openinference.span.kind": "TOOL",
                "tool.name": "lookup",
                "tool.id": "call_1",
                "input.value": '{"q": "rev"}',
                "output.value": "not json {",
            }
        ),
        REFERENCE["openinference"],
    )
    assert span.kind == "tool" and span.tool_name == "lookup" and span.tool_call_id == "call_1"
    assert span.input == {"q": "rev"} and span.output == "not json {"


def test_gen_ai_messages_with_parts():
    messages = [{"role": "user", "parts": [{"type": "text", "content": "Revenue?"}]}]
    out = [
        {
            "role": "assistant",
            "parts": [
                {"type": "tool_call", "id": "c1", "name": "lookup", "arguments": {"q": "rev"}}
            ],
        },
        {"role": "tool", "parts": [{"type": "tool_call_response", "id": "c1", "result": "1.2B"}]},
    ]
    span = to_esf(
        raw(
            {
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": "gpt-5",
                "gen_ai.input.messages": json.dumps(messages),
                "gen_ai.output.messages": json.dumps(out),
                "gen_ai.usage.input_tokens": 7,
                "gen_ai.usage.output_tokens": 3,
            }
        ),
        REFERENCE["gen_ai"],
    )
    assert span.kind == "llm" and span.model == "gpt-5"
    assert span.input_messages[0].text == "Revenue?"
    assert span.output_messages[0].tool_calls[0].arguments == {"q": "rev"}
    assert span.output_messages[1].role == "tool" and span.output_messages[1].tool_call_id == "c1"
    assert span.output_messages[1].text == "1.2B"
    assert span.cost_usd is None and span.unmapped == []


def test_openai_message_format():
    doc = MappingDocument.model_validate(
        {
            "schema_version": "1",
            "kind": {"attribute": "k", "values": {"chat": "llm"}},
            "input_messages": {"attribute": "in", "message_format": "openai"},
            "output_messages": {"attribute": "out", "message_format": "openai"},
        }
    )
    out = [
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
            "tool_calls": [{"id": "c", "function": {"name": "f", "arguments": '{"a": 1}'}}],
        },
        {"role": "tool", "tool_call_id": "c", "content": "ok"},
    ]
    span = to_esf(
        raw(
            {
                "k": "chat",
                "in": json.dumps([{"role": "user", "content": "q"}]),
                "out": json.dumps(out),
            }
        ),
        doc,
    )
    assert span.input_messages[0].text == "q"
    assert span.output_messages[0].text == "hi"
    assert span.output_messages[0].tool_calls[0].arguments == {"a": 1}
    assert span.output_messages[1].tool_call_id == "c"


def test_values_unlisted_becomes_other_and_no_mapping_is_all_unmapped():
    span = to_esf(raw({"openinference.span.kind": "EMBEDDING", "x": 1}), REFERENCE["openinference"])
    assert span.kind == "other" and span.unmapped == ["x"]
    bare = to_esf(raw({"a": 1}), None)
    assert bare.kind == "other" and bare.unmapped == ["a"]


def test_error_status_becomes_error():
    span = to_esf(raw({}, status_code="ERROR", status_message="boom"), None)
    assert span.error == "boom"


def test_mapping_selection():
    custom = MappingDocument(schema_version="1")
    sdk_root = {"evalkit.sdk_root": True, "openinference.span.kind": "CHAIN"}
    assert mapping_for(sdk_root, custom) is SDK_ROOT_MAPPING
    assert mapping_for({"openinference.span.kind": "LLM"}, custom) is custom
    assert mapping_for({"openinference.span.kind": "LLM"}, None) is None
    assert mapping_for({"gen_ai.operation.name": "chat"}, None) is None


def test_session_id_comes_only_from_the_mapping():
    custom = MappingDocument.model_validate(
        {"schema_version": "1", "session_id": {"attribute": "my.session"}}
    )
    assert session_id_of({"my.session": "d", "session.id": "a"}, custom) == "d"
    assert session_id_of({"session.id": "a", "gen_ai.conversation.id": "b"}, None) is None
    assert session_id_of({"session.id": "a"}, custom) is None
    assert session_id_of({"gen_ai.conversation.id": "b"}, REFERENCE["gen_ai"]) == "b"
    sdk_root = {"evalkit.sdk_root": True, "session.id": "r", "my.session": "d"}
    assert session_id_of(sdk_root, custom) == "r"
    task = {"evalkit.task_session": True, "session.id": "t"}
    assert session_id_of(task, REFERENCE["gen_ai"]) == "t"


def test_sdk_root_mapping_reads_input_and_output():
    span = to_esf(
        raw(
            {
                "evalkit.sdk_root": True,
                "openinference.span.kind": "CHAIN",
                "session.id": "r1",
                "input.value": '{"question": "q"}',
                "output.value": "42",
            }
        ),
        SDK_ROOT_MAPPING,
    )
    assert span.kind == "chain" and span.input == {"question": "q"} and span.output == "42"


def test_agent_span_reads_the_call_id_that_spawned_it():
    attributes = {"gen_ai.operation.name": "invoke_agent", "gen_ai.tool.call.id": "call_9"}
    span = to_esf(raw(attributes), REFERENCE["gen_ai"])
    assert span.kind == "agent" and span.tool_call_id == "call_9"
    assert "gen_ai.tool.call.id" not in span.unmapped


GEN_AI_CHAT = {
    "gen_ai.operation.name": "chat",
    "gen_ai.provider.name": "openai",
    "gen_ai.agent.name": "research-agent",
    "gen_ai.request.model": "gpt-5",
    "gen_ai.response.id": "r1",
    "gen_ai.response.finish_reasons": ["tool_calls"],
    "gen_ai.system_instructions": json.dumps([{"type": "text", "content": "Be brief."}]),
    "gen_ai.input.messages": json.dumps(
        [
            {"role": "user", "parts": [{"type": "text", "content": "Revenue?"}]},
            {"role": "tool", "parts": [{"type": "tool_call_response", "id": "c0", "response": 7}]},
        ]
    ),
    "gen_ai.output.messages": json.dumps(
        [
            {
                "role": "assistant",
                "parts": [
                    {"type": "reasoning", "content": "Look it up."},
                    {"type": "tool_call", "id": "c1", "name": "lookup", "arguments": {}},
                ],
            }
        ]
    ),
    "gen_ai.usage.input_tokens": 7,
    "gen_ai.usage.output_tokens": 3,
    "gen_ai.usage.cache_creation.input_tokens": 5,
}


def test_gen_ai_reasoning_system_instructions_response_and_cache_writes():
    span = to_esf(raw(GEN_AI_CHAT), REFERENCE["gen_ai"])
    assert [(m.role, m.text) for m in span.input_messages] == [
        ("system", "Be brief."),
        ("user", "Revenue?"),
        ("tool", "7"),
    ]
    assert span.output_messages[0].reasoning == "Look it up."
    assert span.output_messages[0].text is None
    assert span.cache_write_tokens == 5
    assert span.unmapped == []


def test_gen_ai_tool_response_falls_back_to_result():
    out = [{"role": "tool", "parts": [{"type": "tool_call_response", "id": "c", "result": "ok"}]}]
    attributes = {"gen_ai.operation.name": "chat", "gen_ai.output.messages": json.dumps(out)}
    span = to_esf(raw(attributes), REFERENCE["gen_ai"])
    assert span.output_messages[0].text == "ok"


def test_openinference_reasoning_contents_and_cache_writes():
    attributes = {
        "openinference.span.kind": "LLM",
        "llm.output_messages.0.message.role": "assistant",
        "llm.output_messages.0.message.contents.0.message_content.type": "reasoning",
        "llm.output_messages.0.message.contents.0.message_content.text": "Think.",
        "llm.output_messages.0.message.contents.1.message_content.type": "text",
        "llm.output_messages.0.message.contents.1.message_content.text": "Answer.",
        "llm.token_count.prompt_details.cache_write": 9,
        "llm.provider": "anthropic",
        "llm.invocation_parameters": "{}",
    }
    span = to_esf(raw(attributes), REFERENCE["openinference"])
    message = span.output_messages[0]
    assert (message.reasoning, message.text) == ("Think.", "Answer.")
    assert span.cache_write_tokens == 9 and span.unmapped == []


def test_known_attributes_accept_globs():
    doc = MappingDocument.model_validate(
        {"schema_version": "1", "known_attributes": ["http.*", "service.version"]}
    )
    span = to_esf(raw({"http.method": "GET", "service.version": "1", "x": 1}), doc)
    assert span.unmapped == ["x"]


def test_agent_span_reads_the_agent_name():
    gen_ai = to_esf(
        raw({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "researcher"}),
        REFERENCE["gen_ai"],
    )
    openinference = to_esf(
        raw({"openinference.span.kind": "AGENT", "agent.name": "researcher"}),
        REFERENCE["openinference"],
    )
    llm = to_esf(raw(GEN_AI_CHAT), REFERENCE["gen_ai"])
    assert gen_ai.agent_name == "researcher" and openinference.agent_name == "researcher"
    assert llm.agent_name is None and "gen_ai.agent.name" not in llm.unmapped


def test_input_context_keys_of_the_builtin_mappings():
    assert input_context([REFERENCE["openinference"]], None) == AttributeKeys(
        frozenset({"input.value"}), frozenset({"llm.input_messages."})
    )
    assert input_context([REFERENCE["gen_ai"]], None) == AttributeKeys(
        frozenset({"gen_ai.input.messages", "gen_ai.system_instructions"}), frozenset()
    )
    assert input_context([], None) == AttributeKeys(frozenset(), frozenset())


def test_input_context_never_drops_a_key_an_llm_span_reads_for_something_else():
    doc = MappingDocument.model_validate(
        {
            "schema_version": "1",
            "kind": {"attribute": "k"},
            "model": {"attribute": "shared"},
            "input_messages": {"attribute": "msgs", "message_format": "openinference"},
            "output_messages": {"attribute": "msgs.out", "message_format": "gen_ai"},
            "input": {"attribute": "shared"},
            "system_instructions": {"attribute": "conv"},
        }
    )
    assert input_context([doc], None) == AttributeKeys(frozenset({"conv"}), frozenset())
    keys = input_context([doc], doc.model_copy(update={"session_id": doc.system_instructions}))
    assert keys == AttributeKeys(frozenset(), frozenset())


def test_kind_attributes_cover_what_mapping_for_reads():
    custom = MappingDocument.model_validate({"schema_version": "1", "kind": {"attribute": "k"}})
    assert kind_attributes(custom) == {"evalkit.sdk_root", "openinference.span.kind", "k"}
    assert kind_attributes(None) == {"evalkit.sdk_root", "openinference.span.kind"}
    assert mapping_for({"other": 1}, None) is None


def test_the_sdk_session_attribute_is_not_unmapped_on_sdk_stamped_spans():
    gen_ai = REFERENCE["gen_ai"]
    stamped = {"gen_ai.operation.name": "chat", "session.id": "s1", "evalkit.case_id": "c1"}
    assert to_esf(raw(stamped), gen_ai).unmapped == []
    unstamped = {"gen_ai.operation.name": "chat", "session.id": "s1"}
    assert to_esf(raw(unstamped), gen_ai).unmapped == ["session.id"]
