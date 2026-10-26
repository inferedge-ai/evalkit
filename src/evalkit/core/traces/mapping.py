from __future__ import annotations

import json
import re
from collections.abc import Callable
from fnmatch import fnmatchcase
from typing import Any, NamedTuple

from evalkit.contracts.esf import (
    AttributeRule,
    EsfKind,
    EsfMessage,
    EsfSpan,
    EsfToolCall,
    MappingDocument,
    MessageFormat,
)
from evalkit.core.traces.raw import RawSpan

SDK_ROOT_ATTRIBUTE = "evalkit.sdk_root"
TASK_SESSION_ATTRIBUTE = "evalkit.task_session"
SDK_SESSION_ATTRIBUTE = "session.id"
KINDS: frozenset[str] = frozenset({"llm", "tool", "agent", "chain", "retriever", "other"})


SDK_ROOT_MAPPING = MappingDocument(
    schema_version="1",
    kind=AttributeRule(attribute="openinference.span.kind", values={"CHAIN": "chain"}),
    session_id=AttributeRule(attribute=SDK_SESSION_ATTRIBUTE),
    input=AttributeRule(attribute="input.value", json=True),
    output=AttributeRule(attribute="output.value"),
)


def mapping_for(
    attributes: dict[str, Any], active: MappingDocument | None
) -> MappingDocument | None:
    if attributes.get(SDK_ROOT_ATTRIBUTE):
        return SDK_ROOT_MAPPING
    return active


def kind_attributes(active: MappingDocument | None) -> set[str]:
    docs = [active, SDK_ROOT_MAPPING]
    return {SDK_ROOT_ATTRIBUTE} | {d.kind.attribute for d in docs if d and d.kind}


class AttributeKeys(NamedTuple):
    keys: frozenset[str]
    prefixes: frozenset[str]


def _rule_keys(rules: list[AttributeRule | None]) -> AttributeKeys:
    present = [r for r in rules if r is not None]
    return AttributeKeys(
        frozenset(r.attribute for r in present if r.message_format != "openinference"),
        frozenset(f"{r.attribute}." for r in present if r.message_format == "openinference"),
    )


def _llm_reads(doc: MappingDocument, active: MappingDocument | None) -> AttributeKeys:
    tokens = doc.tokens
    rules = [doc.kind, doc.session_id, doc.model, doc.output_messages, doc.cost_usd]
    rules += [tokens.prompt, tokens.completion, tokens.cached, tokens.cache_write]
    read = _rule_keys(rules)
    linked = {SDK_ROOT_ATTRIBUTE, TASK_SESSION_ATTRIBUTE, SDK_SESSION_ATTRIBUTE}
    if active is not None and active.session_id is not None:
        linked.add(active.session_id.attribute)
    return AttributeKeys(read.keys | linked, read.prefixes)


def input_context(docs: list[MappingDocument], active: MappingDocument | None) -> AttributeKeys:
    context = [_rule_keys([d.input_messages, d.input, d.system_instructions]) for d in docs]
    reads = [_llm_reads(d, active) for d in docs]
    read_keys = {k for r in reads for k in r.keys}
    read_prefixes = {p for r in reads for p in r.prefixes}
    keys = {
        k
        for c in context
        for k in c.keys
        if k not in read_keys and not any(k.startswith(p) for p in read_prefixes)
    }
    prefixes = {
        p
        for c in context
        for p in c.prefixes
        if not any(k.startswith(p) for k in read_keys)
        and not any(p.startswith(q) or q.startswith(p) for q in read_prefixes)
    }
    return AttributeKeys(frozenset(keys), frozenset(prefixes))


def session_id_of(attributes: dict[str, Any], active: MappingDocument | None) -> str | None:
    if attributes.get(SDK_ROOT_ATTRIBUTE) or attributes.get(TASK_SESSION_ATTRIBUTE):
        key: str | None = SDK_SESSION_ATTRIBUTE
    else:
        key = active.session_id.attribute if active and active.session_id else None
    value = attributes.get(key) if key else None
    return None if value in (None, "") else str(value)


def try_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError:
        return value


def _arguments(value: Any) -> dict[str, Any] | str:
    parsed = try_json(value)
    if isinstance(parsed, dict):
        return parsed
    return value if isinstance(value, str) else json.dumps(value)


def _text(content: Any) -> str | None:
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [p.get("text") for p in content if isinstance(p, dict) and p.get("type") == "text"]
        return "".join(p for p in parts if p) or None
    return json.dumps(content)


_INDEXED = re.compile(r"^(\d+)\.(.*)$")


def _content_text(parts: list[dict[str, Any]], kind: str) -> str | None:
    texts = [
        str(p["message_content.text"])
        for p in parts
        if p.get("message_content.type") == kind and p.get("message_content.text") is not None
    ]
    return "".join(texts) or None


def _openinference_messages(
    attributes: dict[str, Any], key: str
) -> tuple[list[EsfMessage], set[str]]:
    prefix = f"{key}."
    read: set[str] = set()
    messages: dict[int, dict[str, Any]] = {}
    calls: dict[int, dict[int, dict[str, Any]]] = {}
    for attr, value in attributes.items():
        if not attr.startswith(prefix):
            continue
        matched = _INDEXED.match(attr[len(prefix) :])
        if matched is None:
            continue
        read.add(attr)
        index, rest = int(matched.group(1)), matched.group(2)
        if rest.startswith("message.tool_calls."):
            call_match = _INDEXED.match(rest.removeprefix("message.tool_calls."))
            if call_match is None:
                continue
            call_index, call_field = int(call_match.group(1)), call_match.group(2)
            calls.setdefault(index, {}).setdefault(call_index, {})[call_field] = value
        else:
            messages.setdefault(index, {})[rest] = value
    out: list[EsfMessage] = []
    for index in sorted(set(messages) | set(calls)):
        fields = messages.get(index, {})
        contents: dict[int, dict[str, Any]] = {}
        for field, value in fields.items():
            content_match = _INDEXED.match(field.removeprefix("message.contents."))
            if field.startswith("message.contents.") and content_match is not None:
                part = contents.setdefault(int(content_match.group(1)), {})
                part[content_match.group(2)] = value
        parts = [contents[j] for j in sorted(contents)]
        tool_calls = [
            EsfToolCall(
                id=str(c.get("tool_call.id") or f"{index}.{j}"),
                name=str(c.get("tool_call.function.name") or ""),
                arguments=_arguments(c.get("tool_call.function.arguments")),
            )
            for j, c in sorted(calls.get(index, {}).items())
        ]
        out.append(
            EsfMessage(
                role=str(fields.get("message.role") or "assistant"),
                text=_text(fields.get("message.content")) or _content_text(parts, "text"),
                reasoning=_content_text(parts, "reasoning"),
                tool_calls=tool_calls or None,
                tool_call_id=fields.get("message.tool_call_id"),
            )
        )
    return out, read


def _gen_ai_messages(attributes: dict[str, Any], key: str) -> tuple[list[EsfMessage], set[str]]:
    raw = try_json(attributes.get(key))
    if not isinstance(raw, list):
        return [], {key}
    out: list[EsfMessage] = []
    for message in raw:
        if not isinstance(message, dict):
            continue
        texts: list[str] = []
        reasoning: list[str] = []
        tool_calls: list[EsfToolCall] = []
        tool_call_id: str | None = None
        for part in message.get("parts") or []:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text" and part.get("content") is not None:
                texts.append(str(part["content"]))
            elif kind == "reasoning" and part.get("content") is not None:
                reasoning.append(str(part["content"]))
            elif kind == "tool_call":
                tool_calls.append(
                    EsfToolCall(
                        id=str(part.get("id") or ""),
                        name=str(part.get("name") or ""),
                        arguments=_arguments(part.get("arguments")),
                    )
                )
            elif kind == "tool_call_response":
                tool_call_id = str(part.get("id") or "") or None
                result = part["response"] if "response" in part else part.get("result")
                texts.append(result if isinstance(result, str) else json.dumps(result))
        out.append(
            EsfMessage(
                role=str(message.get("role") or "assistant"),
                text="".join(texts) or None,
                reasoning="".join(reasoning) or None,
                tool_calls=tool_calls or None,
                tool_call_id=tool_call_id,
            )
        )
    return out, {key}


def gen_ai_messages(value: Any) -> list[EsfMessage] | None:
    if not isinstance(value, list) or not value:
        return None
    if not all(isinstance(m, dict) and isinstance(m.get("parts"), list) for m in value):
        return None
    return _gen_ai_messages({"messages": value}, "messages")[0]


def _instructions(value: Any) -> str | None:
    if isinstance(value, list):
        texts = [
            str(p.get("content") if p.get("content") is not None else p.get("text") or "")
            for p in value
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        return "".join(texts) or None
    return _str(value) or None


def _openai_messages(attributes: dict[str, Any], key: str) -> tuple[list[EsfMessage], set[str]]:
    raw = try_json(attributes.get(key))
    if not isinstance(raw, list):
        return [], {key}
    out: list[EsfMessage] = []
    for message in raw:
        if not isinstance(message, dict):
            continue
        tool_calls = [
            EsfToolCall(
                id=str(c.get("id") or ""),
                name=str((c.get("function") or {}).get("name") or ""),
                arguments=_arguments((c.get("function") or {}).get("arguments")),
            )
            for c in message.get("tool_calls") or []
            if isinstance(c, dict)
        ]
        out.append(
            EsfMessage(
                role=str(message.get("role") or "assistant"),
                text=_text(message.get("content")),
                tool_calls=tool_calls or None,
                tool_call_id=message.get("tool_call_id"),
            )
        )
    return out, {key}


MESSAGE_FORMATS: dict[
    MessageFormat, Callable[[dict[str, Any], str], tuple[list[EsfMessage], set[str]]]
] = {
    "openinference": _openinference_messages,
    "gen_ai": _gen_ai_messages,
    "openai": _openai_messages,
}


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


def _float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _str(value: Any) -> str | None:
    return None if value is None else str(value)


def to_esf(raw: RawSpan, mapping: MappingDocument | None) -> EsfSpan:
    read: set[str] = set()
    attributes = raw.attributes

    def rule(r: AttributeRule | None) -> Any:
        if r is None:
            return None
        if r.message_format is not None:
            messages, keys = MESSAGE_FORMATS[r.message_format](attributes, r.attribute)
            read.update(keys)
            return messages
        read.add(r.attribute)
        value = attributes.get(r.attribute)
        if r.values is not None:
            value = r.values.get(str(value), "other") if value is not None else None
        if r.json_:
            value = try_json(value)
        return value

    doc = mapping if mapping is not None else MappingDocument(schema_version="1")
    known = doc.known_attributes
    rule(doc.session_id)
    kind_value = rule(doc.kind)
    kind: EsfKind = kind_value if kind_value in KINDS else "other"
    is_llm = kind == "llm"
    span = EsfSpan(
        span_id=raw.span_id,
        trace_id=raw.trace_id,
        parent_id=raw.parent_span_id,
        name=raw.name,
        kind=kind,
        started_at=raw.start_time,
        ended_at=raw.end_time,
        error=(raw.status_message or "error") if raw.status_code == "ERROR" else None,
        model=_str(rule(doc.model)),
        input_messages=rule(doc.input_messages) if is_llm else None,
        output_messages=rule(doc.output_messages) if is_llm else None,
        input=rule(doc.input) if not is_llm else None,
        output=rule(doc.output) if not is_llm else None,
        tool_name=_str(rule(doc.tool.name)) if kind == "tool" else None,
        tool_call_id=_str(rule(doc.tool.call_id)) if kind in ("tool", "agent") else None,
        agent_name=_str(rule(doc.agent_name)) if kind == "agent" else None,
        prompt_tokens=_int(rule(doc.tokens.prompt)),
        completion_tokens=_int(rule(doc.tokens.completion)),
        cached_tokens=_int(rule(doc.tokens.cached)),
        cache_write_tokens=_int(rule(doc.tokens.cache_write)),
        cost_usd=_float(rule(doc.cost_usd)),
    )
    if is_llm and (system := _instructions(rule(doc.system_instructions))):
        span.input_messages = [EsfMessage(role="system", text=system), *(span.input_messages or [])]
    if kind == "tool":
        if doc.tool.arguments is not None:
            span.input = rule(doc.tool.arguments)
        if doc.tool.result is not None:
            span.output = rule(doc.tool.result)
    sdk_stamped = any(k.startswith("evalkit.") for k in attributes)
    span.unmapped = [
        k
        for k in attributes
        if k not in read
        and not k.startswith("evalkit.")
        and not (sdk_stamped and k == SDK_SESSION_ATTRIBUTE)
        and not any(fnmatchcase(k, pattern) for pattern in known)
    ]
    return span
