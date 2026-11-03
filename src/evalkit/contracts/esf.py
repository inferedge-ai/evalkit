from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

EsfKind = Literal["llm", "tool", "agent", "chain", "retriever", "other"]
MessageFormat = Literal["openinference", "gen_ai", "openai"]
AgentRole = Literal["main", "subagent"]
MAPPING_SCHEMA_VERSION = "1"


class EsfToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] | str


class EsfMessage(BaseModel):
    role: str
    text: str | None = None
    reasoning: str | None = None
    tool_calls: list[EsfToolCall] | None = None
    tool_call_id: str | None = None


class EsfSpan(BaseModel):
    span_id: str
    trace_id: str
    parent_id: str | None
    name: str
    kind: EsfKind
    started_at: datetime
    ended_at: datetime | None
    error: str | None
    model: str | None = None
    input_messages: list[EsfMessage] | None = None
    output_messages: list[EsfMessage] | None = None
    input: Any | None = None
    output: Any | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    agent_name: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    cost_usd: float | None = None
    scope: str | None = None
    excluded: bool = False
    agent_role: AgentRole | None = None
    unmapped: list[str] = Field(default_factory=list)


class MappingModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AttributeRule(MappingModel):
    attribute: str = Field(description="The span attribute key to read.")
    json_: bool = Field(
        default=False,
        alias="json",
        description="Parse the value with json.loads when it is a string; keep the string "
        "on failure.",
    )
    values: dict[str, str] | None = Field(
        default=None, description="Translate the read value through this table."
    )
    message_format: MessageFormat | None = Field(
        default=None,
        description="Collect messages laid out per this format under the attribute key.",
    )


class SpanMatch(MappingModel):
    name: str | None = Field(
        default=None, description="Span name; glob patterns such as OpenAILike.* are allowed."
    )
    attribute: str | None = Field(default=None, description="A span attribute key to compare.")
    equals: Any | None = Field(
        default=None,
        description="The value the attribute must hold. Leave out to match any span that has "
        "the attribute.",
    )
    scope: str | None = Field(
        default=None,
        description="Instrumentation scope name, e.g. mcp; glob patterns are allowed.",
    )

    @model_validator(mode="after")
    def require_a_field(self) -> SpanMatch:
        if self.name is None and self.attribute is None and self.scope is None:
            raise ValueError("a span match needs name, attribute or scope")
        if self.equals is not None and self.attribute is None:
            raise ValueError("equals needs attribute")
        return self


class SpanRekind(SpanMatch):
    kind: EsfKind = Field(description="The kind matching spans get instead of the kind rule's.")


class TokenRules(MappingModel):
    prompt: AttributeRule | None = None
    completion: AttributeRule | None = None
    cached: AttributeRule | None = None
    cache_write: AttributeRule | None = None


class ToolRules(MappingModel):
    name: AttributeRule | None = None
    call_id: AttributeRule | None = None
    arguments: AttributeRule | None = None
    result: AttributeRule | None = None


class MappingDocument(MappingModel):
    schema_version: Literal["1"]
    kind: AttributeRule | None = None
    session_id: AttributeRule | None = None
    model: AttributeRule | None = None
    system_instructions: AttributeRule | None = Field(
        default=None,
        description="System prompt kept outside the input messages: a string or a list of "
        "text parts. Read on llm spans and put first in the input messages.",
    )
    input_messages: AttributeRule | None = None
    output_messages: AttributeRule | None = None
    tokens: TokenRules = Field(default_factory=TokenRules)
    cost_usd: AttributeRule | None = None
    tool: ToolRules = Field(default_factory=ToolRules)
    agent_name: AttributeRule | None = Field(
        default=None,
        description="The agent's name on agent spans; names the subagent trajectory. The span "
        "name is used when it is absent.",
    )
    input: AttributeRule | None = None
    output: AttributeRule | None = None
    known_attributes: list[str] = Field(
        default_factory=list,
        description="Attributes no rule reads that are expected and need nothing: not reported "
        "as unmapped. Glob patterns such as llm.tools.* are allowed.",
    )
    answer: str = Field(
        default="root",
        description="Where the run's answer comes from: root, a span name, or "
        "<span name>.<output key>.",
    )
    exclude_spans: list[str | SpanMatch] = Field(
        default_factory=list,
        description="Spans left out of the trajectory: a span name (glob patterns allowed) or "
        "a match on name, attribute and scope; every field given must match.",
    )
    rekind_spans: list[SpanRekind] = Field(
        default_factory=list,
        description="Spans that get another kind than the kind rule gives, e.g. a graph node "
        "tagged agent that is only a step: {name: agent, kind: chain}. The first match wins.",
    )
    main_agent: str | SpanMatch | None = Field(
        default=None,
        description="The agent span whose answer and id the trajectory carries, when the "
        "earliest top-level agent span is not it.",
    )
    subagent_spans: list[str | SpanMatch] = Field(
        default_factory=list,
        description="Agent spans that become subagent trajectories even when no tool span "
        "spawns them, e.g. sibling crew members to keep apart.",
    )
