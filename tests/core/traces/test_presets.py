import json

import pytest

from evalkit.contracts.esf import MappingDocument
from evalkit.core.traces.presets import list_preset_names, read_preset

GENERIC = {"openinference", "gen_ai"}

FRAMEWORK_MAPPINGS = {
    "langchain": (
        "openinference",
        ["input.value", "output.value", "llm.input_messages.*", "llm.output_messages.*"],
        {},
    ),
    "crewai": (
        "openinference",
        [
            "agent.backstory",
            "agent.goal",
            "agent.tools",
            "conversation.turn_index",
            "tool.cache_function",
            "tool.current_usage_count",
            "tool.description_updated",
            "tool.result_as_answer",
        ],
        {},
    ),
    "agno": ("openinference", ["agno.*"], {}),
    "llamaindex": (
        "openinference",
        ["input.value", "output.value"],
        {
            "rekind_spans": [
                {"name": "*.astream_chat", "attribute": "llm.finish_reason", "kind": "llm"},
                {"name": "*.astream_chat", "kind": "chain"},
                {"name": "*._prepare_chat_with_tools", "kind": "chain"},
            ]
        },
    ),
    "google-adk": (
        "gen_ai",
        ["gcp.vertex.agent.*", "input.value", "output.value", "session.id"],
        {"exclude_spans": ["execute_tool (merged)"]},
    ),
    "claude-agent-sdk": (
        "gen_ai",
        ["mcp.*", "jsonrpc.*"],
        {"exclude_spans": [{"scope": "mcp-python-sdk"}]},
    ),
    "mastra": (
        "gen_ai",
        ["mastra.*", "gen_ai.usage.reasoning_tokens", "gen_ai.tool.call.id"],
        {
            "answer": "invoke_agent <agent name>.text",
            "exclude_spans": [
                {"attribute": "mastra.span.type", "equals": kind}
                for kind in ("processor_run", "memory_operation", "model_chunk")
            ],
        },
    ),
}


def test_every_preset_is_a_valid_mapping_document():
    names = list_preset_names()
    assert names == sorted(GENERIC | set(FRAMEWORK_MAPPINGS))
    for name in names:
        MappingDocument.model_validate_json(read_preset(name))


def test_framework_presets_are_their_base_plus_the_listed_changes():
    for name, (base_name, known, overrides) in FRAMEWORK_MAPPINGS.items():
        base = json.loads(read_preset(base_name))
        expected = base | {"known_attributes": base["known_attributes"] + known} | overrides
        assert json.loads(read_preset(name)) == expected, name


def test_read_preset_refuses_a_name_that_is_not_a_preset():
    for name in ("nope", "gen_ai.json", "../mapping_presets/gen_ai", "GEN_AI"):
        with pytest.raises(KeyError):
            read_preset(name)
