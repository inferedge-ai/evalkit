from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EsfKind = Literal["llm", "tool", "agent", "chain", "retriever", "other"]
MessageFormat = Literal["openinference", "gen_ai", "openai"]
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
    exclude_spans: list[str] = Field(
        default_factory=list,
        description="Span names left out of the trajectory.",
    )
