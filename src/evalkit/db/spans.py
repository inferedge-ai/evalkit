from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, NamedTuple

from pydantic import BaseModel
from sqlalchemy import (
    BigInteger,
    ColumnElement,
    Text,
    and_,
    any_,
    case,
    cast,
    func,
    not_,
    or_,
    select,
    text,
    tuple_,
    type_coerce,
)
from sqlalchemy.dialects.postgresql import ARRAY, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.core.agents import UnknownAgent
from evalkit.core.traces.mapping import SDK_ROOT_ATTRIBUTE, AttributeKeys
from evalkit.core.traces.raw import RawSpan
from evalkit.db import models
from evalkit.db.runs import RunSource, eval_run_ids
from evalkit.db.session import tenant_session

UPSERT_CHUNK = 1000
SPAN_KEY = ("agent_id", "trace_id", "span_id")
LOCK_TRACES = text(
    "SELECT pg_advisory_xact_lock(hashtext(n)) "
    "FROM unnest(CAST(:names AS text[])) WITH ORDINALITY AS t(n, i) ORDER BY i"
)
SESSION_FROM_ANCESTORS = text(
    """
    WITH RECURSIVE trace AS (
        SELECT id, trace_id, span_id, parent_span_id, session_id
        FROM spans
        WHERE agent_id = :agent_id AND trace_id = ANY(:trace_ids)
    ),
    chain AS (
        SELECT t.id, t.trace_id, t.span_id, t.session_id AS resolved
        FROM trace t
        WHERE NOT EXISTS (
            SELECT 1 FROM trace p
            WHERE p.trace_id = t.trace_id AND p.span_id = t.parent_span_id
        )
        UNION ALL
        SELECT t.id, t.trace_id, t.span_id, coalesce(c.resolved, t.session_id)
        FROM trace t
        JOIN chain c ON t.trace_id = c.trace_id AND t.parent_span_id = c.span_id
    ),
    roots AS (
        SELECT trace_id, max(session_id) AS session_id
        FROM trace
        WHERE parent_span_id IS NULL
        GROUP BY trace_id
    )
    UPDATE spans
    SET session_id = coalesce(r.session_id, c.resolved)
    FROM chain c LEFT JOIN roots r ON r.trace_id = c.trace_id
    WHERE spans.id = c.id
      AND spans.session_id IS DISTINCT FROM coalesce(r.session_id, c.resolved)
    """
)


class SessionSummary(BaseModel):
    session_id: str | None
    trace_id: str
    title: str | None
    created_at: datetime
    span_count: int
    sdk_root: dict[str, Any] | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


SPAN_COLUMNS = (
    models.Span.trace_id,
    models.Span.span_id,
    models.Span.parent_span_id,
    models.Span.name,
    models.Span.kind,
    models.Span.start_time,
    models.Span.end_time,
    models.Span.status_code,
    models.Span.status_message,
    models.Span.attributes,
    models.Span.scope,
    models.Span.received_at,
)
HEAD_COLUMNS = tuple(c for c in SPAN_COLUMNS if c is not models.Span.attributes)
STREAM_BATCH = 4
SESSION_ORDER = (models.Span.start_time, models.Span.id)


def _to_raw(row: Mapping[Any, Any]) -> RawSpan:
    start = _aware(row["start_time"])
    assert start is not None
    return RawSpan(
        trace_id=row["trace_id"],
        span_id=row["span_id"],
        parent_span_id=row["parent_span_id"],
        name=row["name"],
        kind=row["kind"],
        start_time=start,
        end_time=_aware(row["end_time"]),
        status_code=row["status_code"],
        status_message=row["status_message"],
        attributes=row["attributes"],
        events=row.get("events", []),
        resource=row.get("resource", {}),
        scope=row["scope"],
        received_at=_aware(row["received_at"]),
    )


class AttributeDrop(NamedTuple):
    rows: list[int]
    keys: AttributeKeys


class SessionRead:
    def __init__(self, session: AsyncSession, where: ColumnElement[bool]):
        self._session = session
        self._where = where

    async def probes(self, keys: set[str]) -> list[tuple[int, RawSpan]]:
        ordered = sorted(keys)
        values = [models.Span.attributes[key].label(f"probe_{i}") for i, key in enumerate(ordered)]
        statement = (
            select(models.Span.id, *HEAD_COLUMNS, *values)
            .where(self._where)
            .order_by(*SESSION_ORDER)
        )
        out: list[tuple[int, RawSpan]] = []
        for row in (await self._session.execute(statement)).mappings():
            found = {key: row[f"probe_{i}"] for i, key in enumerate(ordered)}
            attributes = {key: value for key, value in found.items() if value is not None}
            out.append((row["id"], _to_raw({**row, "attributes": attributes})))
        return out

    def _attributes(self, drop: AttributeDrop) -> ColumnElement[Any]:
        attributes = models.Span.attributes.expression
        if not drop.rows or not (drop.keys.keys or drop.keys.prefixes):
            return attributes
        each = func.jsonb_each(attributes).table_valued("key", "value")
        rebuilt = func.jsonb_object_agg(each.c.key, each.c.value)
        dropped = models.Span.id == any_(cast(drop.rows, ARRAY(BigInteger)))
        removed = or_(
            each.c.key.in_(sorted(drop.keys.keys)),
            *[func.substr(each.c.key, 1, len(p)) == p for p in sorted(drop.keys.prefixes)],
        )
        kept = select(rebuilt).select_from(each).where(not_(removed)).scalar_subquery()
        return type_coerce(case((dropped, kept), else_=attributes), models.Span.attributes.type)

    async def roots_received(self) -> bool:
        traces = select(models.Span.agent_id, models.Span.trace_id).where(self._where).distinct()
        found = (await self._session.execute(traces)).all()
        if not found:
            return False
        roots = select(func.count(func.distinct(models.Span.trace_id))).where(
            tuple_(models.Span.agent_id, models.Span.trace_id).in_(found),
            models.Span.parent_span_id.is_(None),
        )
        return (await self._session.execute(roots)).scalar_one() == len(found)

    async def attribute_bytes(self, drop: AttributeDrop) -> int:
        size = func.octet_length(cast(self._attributes(drop), Text))
        statement = select(func.coalesce(func.sum(size), 0)).where(self._where)
        return int((await self._session.execute(statement)).scalar_one())

    async def stream(self, drop: AttributeDrop) -> AsyncIterator[RawSpan]:
        statement = (
            select(*HEAD_COLUMNS, self._attributes(drop).label("attributes"))
            .where(self._where)
            .order_by(*SESSION_ORDER)
            .execution_options(yield_per=STREAM_BATCH)
        )
        result = await self._session.stream(statement)
        async for row in result.mappings():
            yield _to_raw(row)


class SpanStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tenant_id: str):
        self._sessions = session_factory
        self._tenant_id = tenant_id

    def _session(self):
        return tenant_session(self._sessions, self._tenant_id)

    async def _check_agent(self, session: AsyncSession, agent_id: int) -> None:
        found = await session.scalar(
            select(models.Agent.id)
            .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
            .where(models.Workspace.tenant_id == self._tenant_id, models.Agent.id == agent_id)
        )
        if found is None:
            raise UnknownAgent(str(agent_id))

    async def upsert(
        self, agent_id: int, spans: list[RawSpan], *, external_trace_source_id: int | None = None
    ) -> int:
        if not spans:
            return 0
        trace_ids = sorted({s.trace_id for s in spans})
        async with self._session() as session:
            await self._check_agent(session, agent_id)
            await session.execute(
                LOCK_TRACES, {"names": [f"spans:{agent_id}:{t}" for t in trace_ids]}
            )
            now = datetime.now(UTC)
            rows = {
                (span.trace_id, span.span_id): dict(
                    agent_id=agent_id,
                    trace_id=span.trace_id,
                    span_id=span.span_id,
                    parent_span_id=span.parent_span_id,
                    session_id=span.session_id_hint,
                    name=span.name,
                    kind=span.kind,
                    start_time=span.start_time,
                    end_time=span.end_time,
                    status_code=span.status_code,
                    status_message=span.status_message,
                    attributes=span.attributes,
                    events=span.events,
                    resource=span.resource,
                    scope=span.scope,
                    received_at=now,
                    external_trace_source_id=external_trace_source_id,
                )
                for span in spans
            }
            values = list(rows.values())
            for start in range(0, len(values), UPSERT_CHUNK):
                statement = insert(models.Span).values(values[start : start + UPSERT_CHUNK])
                await session.execute(
                    statement.on_conflict_do_update(
                        index_elements=list(SPAN_KEY),
                        set_={k: statement.excluded[k] for k in values[0] if k not in SPAN_KEY},
                    )
                )
            await session.execute(
                SESSION_FROM_ANCESTORS,
                {"agent_id": agent_id, "trace_ids": trace_ids},
            )
            await session.commit()
        return len(spans)

    def _trace_ids_of(self, agent_id: int, session_id: str):
        return select(models.Span.trace_id).where(
            models.Span.agent_id == agent_id,
            models.Span.session_id == session_id,
            models.Span.parent_span_id.is_(None),
        )

    def _in_session(self, agent_id: int, session_id: str) -> ColumnElement[bool]:
        return and_(
            models.Span.agent_id == agent_id,
            or_(
                models.Span.session_id == session_id,
                models.Span.trace_id == session_id,
                models.Span.trace_id.in_(self._trace_ids_of(agent_id, session_id)),
            ),
        )

    @asynccontextmanager
    async def read_session(self, agent_id: int, session_id: str) -> AsyncIterator[SessionRead]:
        async with self._session() as session:
            await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            await self._check_agent(session, agent_id)
            yield SessionRead(session, self._in_session(agent_id, session_id))

    async def stream_session_spans(self, agent_id: int, session_id: str) -> AsyncIterator[RawSpan]:
        statement = (
            select(*SPAN_COLUMNS, models.Span.events, models.Span.resource)
            .where(self._in_session(agent_id, session_id))
            .order_by(*SESSION_ORDER)
            .execution_options(yield_per=STREAM_BATCH)
        )
        async with self._session() as session:
            await self._check_agent(session, agent_id)
            result = await session.stream(statement)
            async for row in result.mappings():
                yield _to_raw(row)

    async def session_spans(self, agent_id: int, session_id: str) -> list[RawSpan]:
        return [span async for span in self.stream_session_spans(agent_id, session_id)]

    async def list_sessions(
        self,
        agent_id: int,
        *,
        query: str | None,
        limit: int,
        offset: int,
        since: datetime | None = None,
        until: datetime | None = None,
        linked: RunSource = "all",
    ) -> tuple[list[SessionSummary], int]:
        where = [models.Span.agent_id == agent_id, models.Span.session_id.is_not(None)]
        if query:
            where.append(models.Span.session_id.startswith(query))
        if linked == "eval":
            where.append(models.Span.session_id.in_(eval_run_ids(agent_id)))
        elif linked == "app":
            where.append(models.Span.session_id.not_in(eval_run_ids(agent_id)))
        newest = func.max(models.Span.start_time)
        started = func.min(models.Span.start_time)
        grouped = (
            select(
                models.Span.session_id,
                started.label("started"),
                newest.label("newest"),
                func.count().label("n"),
            )
            .where(*where)
            .group_by(models.Span.session_id)
        )
        if since is not None:
            grouped = grouped.having(started >= since)
        if until is not None:
            grouped = grouped.having(started < until)
        async with self._session() as session:
            await self._check_agent(session, agent_id)
            total = (
                await session.execute(select(func.count()).select_from(grouped.subquery()))
            ).scalar_one()
            page = (
                await session.execute(grouped.order_by(newest.desc()).limit(limit).offset(offset))
            ).all()
            ids = [row.session_id for row in page]
            titles: dict[str, str] = {}
            sdk_roots: dict[str, dict[str, Any]] = {}
            traces: dict[str, str] = {}
            if ids:
                roots = await session.execute(
                    select(models.Span.session_id, models.Span.name, models.Span.attributes)
                    .where(
                        models.Span.agent_id == agent_id,
                        models.Span.session_id.in_(ids),
                        models.Span.parent_span_id.is_(None),
                    )
                    .order_by(models.Span.start_time)
                )
                for session_id, name, attributes in roots.all():
                    titles.setdefault(session_id, name)
                    if attributes.get(SDK_ROOT_ATTRIBUTE):
                        sdk_roots.setdefault(session_id, attributes)
                first = await session.execute(
                    select(models.Span.session_id, models.Span.trace_id)
                    .where(models.Span.agent_id == agent_id, models.Span.session_id.in_(ids))
                    .order_by(models.Span.start_time)
                )
                for session_id, trace_id in first.all():
                    traces.setdefault(session_id, trace_id)
        return [
            SessionSummary(
                session_id=row.session_id,
                trace_id=traces.get(row.session_id, ""),
                title=titles.get(row.session_id),
                created_at=_aware(row.started) or row.started,
                span_count=row.n,
                sdk_root=sdk_roots.get(row.session_id),
            )
            for row in page
        ], total

    async def list_unsessioned(
        self, agent_id: int, *, limit: int, offset: int
    ) -> tuple[list[SessionSummary], int]:
        where = [models.Span.agent_id == agent_id, models.Span.session_id.is_(None)]
        started = func.min(models.Span.start_time)
        grouped = (
            select(models.Span.trace_id, started.label("started"), func.count().label("n"))
            .where(*where)
            .group_by(models.Span.trace_id)
        )
        async with self._session() as session:
            await self._check_agent(session, agent_id)
            total = (
                await session.execute(select(func.count()).select_from(grouped.subquery()))
            ).scalar_one()
            page = (
                await session.execute(grouped.order_by(started.desc()).limit(limit).offset(offset))
            ).all()
        return [
            SessionSummary(
                session_id=None,
                trace_id=row.trace_id,
                title=None,
                created_at=_aware(row.started) or row.started,
                span_count=row.n,
            )
            for row in page
        ], total
