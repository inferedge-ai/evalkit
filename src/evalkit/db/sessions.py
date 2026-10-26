from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Literal

from evalkit.config import OtelConfig
from evalkit.contracts.esf import MappingDocument
from evalkit.core.agents import AgentDefinition, explicit
from evalkit.core.traces.raw import RawSpan
from evalkit.core.traces.sessions import (
    SessionBuilder,
    SessionTooLarge,
    SessionTrajectory,
    later_llm_spans,
    probe_attributes,
)
from evalkit.core.workspace import parse_slug
from evalkit.db.agents import AgentStore
from evalkit.db.mappings import MappingStore
from evalkit.db.spans import AttributeDrop, SpanStore

CONCURRENT_READS = 2


def _mib(size: int) -> str:
    return f"{size / (1024 * 1024):.1f} MiB"


class DbSessionReader:
    def __init__(
        self, agents: AgentStore, spans: SpanStore, mappings: MappingStore, otel: OtelConfig
    ):
        self._agents = agents
        self._spans = spans
        self._mappings = mappings
        self._otel = otel
        self._reads = asyncio.Semaphore(CONCURRENT_READS)

    async def agent(self, agent_slug: str) -> AgentDefinition:
        return await self._agents.get(explicit(parse_slug(agent_slug)))

    def raw(self, record: AgentDefinition, session_id: str) -> AsyncIterator[RawSpan]:
        return self._spans.stream_session_spans(record.id, session_id)

    async def read(
        self,
        agent_slug: str,
        session_id: str,
        *,
        mapping: Literal["active", "draft"] = "active",
        include_esf: bool = False,
    ) -> SessionTrajectory | None:
        record = await self.agent(agent_slug)
        chosen = await (
            self._mappings.draft(record.id)
            if mapping == "draft"
            else self._mappings.active(record.id)
        )
        document = MappingDocument.model_validate(chosen.document) if chosen else None
        async with self._reads, self._spans.read_session(record.id, session_id) as rows:
            probes = await rows.probes(probe_attributes(document))
            later = later_llm_spans([span for _, span in probes], document)
            drop = AttributeDrop([probes[i][0] for i in later.positions], later.context)
            size = await rows.attribute_bytes(drop)
            limit = self._otel.max_session_bytes
            if size > limit:
                raise SessionTooLarge(
                    f"session {session_id} of agent {agent_slug} holds {_mib(size)} of span "
                    f"attributes, over this server's otel.max_session_bytes ({_mib(limit)}); "
                    "ask the deployment's admin to raise the limit (and the server's memory), "
                    "or record less per span"
                )
            builder = SessionBuilder(document)
            async for span in rows.stream(drop):
                builder.add(span)
            return builder.build(
                agent_name=record.name,
                mapping_version=chosen.version if chosen else None,
                now=datetime.now(UTC),
                settle_seconds=self._otel.settle_seconds,
                include_esf=include_esf,
            )

    async def trajectory(self, agent: str, session_id: str) -> SessionTrajectory:
        result = await self.read(agent, session_id)
        if result is None:
            return SessionTrajectory(settled=False, root_present=False, span_count=0)
        return result
