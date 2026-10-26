from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any, NamedTuple, Protocol

from pydantic import BaseModel, Field

from evalkit.contracts.esf import EsfSpan, MappingDocument
from evalkit.core.traces.convert import first_llm_spans, to_atif
from evalkit.core.traces.mapping import (
    SDK_ROOT_ATTRIBUTE,
    TASK_SESSION_ATTRIBUTE,
    AttributeKeys,
    input_context,
    kind_attributes,
    mapping_for,
    session_id_of,
    to_esf,
)
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.settle import root_present, settled

LINK_ATTRIBUTES = (SDK_ROOT_ATTRIBUTE, TASK_SESSION_ATTRIBUTE)


class SessionTrajectory(BaseModel):
    settled: bool
    root_present: bool
    span_count: int
    mapping_version: int | None = None
    unmapped_attributes: list[str] = Field(default_factory=list)
    trajectory: dict[str, Any] | None = None
    esf: list[EsfSpan] | None = None


class SessionReader(Protocol):
    async def trajectory(self, agent: str, session_id: str) -> SessionTrajectory: ...


def agent_spans(spans: list[RawSpan]) -> list[RawSpan]:
    linked = [
        s
        for s in spans
        if s.parent_span_id is None
        and s.attributes.get(SDK_ROOT_ATTRIBUTE)
        and s.attributes.get(TASK_SESSION_ATTRIBUTE)
    ]
    if not linked:
        return spans
    linked_traces = {s.trace_id for s in linked}
    others = [s for s in spans if s.trace_id not in linked_traces]
    if others:
        return others
    parents = {s.parent_span_id for s in spans}
    if any(root.span_id in parents for root in linked):
        return spans
    return []


class SessionTooLarge(Exception):
    pass


class LaterLlmSpans(NamedTuple):
    positions: list[int]
    context: AttributeKeys


def probe_attributes(active: MappingDocument | None) -> set[str]:
    return kind_attributes(active) | set(LINK_ATTRIBUTES)


def later_llm_spans(probes: list[RawSpan], active: MappingDocument | None) -> LaterLlmSpans:
    position = {id(span): i for i, span in enumerate(probes)}
    kept = agent_spans(probes)
    mappings = [mapping_for(span.attributes, active) for span in kept]
    esf = [to_esf(span, mapping) for span, mapping in zip(kept, mappings, strict=True)]
    first = {id(span) for span in first_llm_spans(esf, active)}
    later = [
        (position[id(raw)], mapping)
        for raw, mapping, span in zip(kept, mappings, esf, strict=True)
        if span.kind == "llm" and id(span) not in first and mapping is not None
    ]
    docs = list({id(mapping): mapping for _, mapping in later}.values())
    return LaterLlmSpans([i for i, _ in later], input_context(docs, active))


class SessionBuilder:
    def __init__(self, active_mapping: MappingDocument | None):
        self._active_mapping = active_mapping
        self._texts: dict[str, str] = {}
        self._spans: list[tuple[RawSpan, EsfSpan, str | None]] = []

    def _shared(self, text: str | None) -> str | None:
        return None if text is None else self._texts.setdefault(text, text)

    def add(self, raw: RawSpan) -> None:
        attributes = raw.attributes
        esf = to_esf(raw, mapping_for(attributes, self._active_mapping))
        for message in [*(esf.input_messages or []), *(esf.output_messages or [])]:
            message.text = self._shared(message.text)
            message.reasoning = self._shared(message.reasoning)
        head = raw.model_copy(
            update={
                "attributes": {k: attributes[k] for k in LINK_ATTRIBUTES if k in attributes},
                "events": [],
                "resource": {},
            }
        )
        self._spans.append((head, esf, session_id_of(attributes, self._active_mapping)))

    def build(
        self,
        *,
        agent_name: str,
        mapping_version: int | None,
        now: datetime,
        settle_seconds: int,
        include_esf: bool = False,
    ) -> SessionTrajectory:
        kept_heads = {id(head) for head in agent_spans([head for head, _, _ in self._spans])}
        kept = [entry for entry in self._spans if id(entry[0]) in kept_heads]
        self._spans = []
        self._texts = {}
        heads = [head for head, _, _ in kept]
        esf = [span for _, span, _ in kept]
        unmapped = sorted({key for s in esf for key in s.unmapped})
        result = SessionTrajectory(
            settled=settled(heads, now, settle_seconds),
            root_present=root_present(heads),
            span_count=len(heads),
            mapping_version=mapping_version,
            unmapped_attributes=unmapped,
            esf=esf if include_esf else None,
        )
        if not heads:
            return result
        received = [s.received_at for s in heads if s.received_at is not None]
        session_id = next((found for _, _, found in kept if found), None)
        trajectory = to_atif(
            esf,
            self._active_mapping,
            agent_name=agent_name,
            session_id=session_id,
            mapping_version=mapping_version,
            incomplete_ended_at=max(received) if received else now,
        )
        result.trajectory = trajectory.model_dump(exclude_none=True)
        return result


def build_session_trajectory(
    spans: Iterable[RawSpan],
    *,
    agent_name: str,
    active_mapping: MappingDocument | None,
    mapping_version: int | None,
    now: datetime,
    settle_seconds: int,
    include_esf: bool = False,
) -> SessionTrajectory:
    builder = SessionBuilder(active_mapping)
    for span in spans:
        builder.add(span)
    return builder.build(
        agent_name=agent_name,
        mapping_version=mapping_version,
        now=now,
        settle_seconds=settle_seconds,
        include_esf=include_esf,
    )
