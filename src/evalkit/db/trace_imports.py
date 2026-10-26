from datetime import UTC, datetime, timedelta

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import TraceImportStatus
from evalkit.core.traces.importer import (
    ACTIVE_IMPORT,
    IMPORT_LEASE_SECONDS,
    IMPORT_STOPPED_ERROR,
    ImportJob,
)
from evalkit.db import models
from evalkit.db.locks import lock_name
from evalkit.store.base import Conflict

ACTIVE = [s.value for s in ACTIVE_IMPORT]
RESTARTABLE = [TraceImportStatus.FAILED.value, TraceImportStatus.STOPPED.value]


class UnknownTraceImport(KeyError):
    def __init__(self, import_id: int):
        super().__init__(f"unknown trace import: {import_id}")


class ImportRunning(Conflict):
    def __init__(self, import_id: int):
        super().__init__(f"import {import_id} is already running for this agent")


class ImportNotActive(Conflict):
    def __init__(self, import_id: int, status: str, action: str):
        super().__init__(f"import {import_id} is {status}; only {action}")


class TraceImportView(BaseModel):
    id: int
    agent: str
    source: str
    project: str
    since: datetime
    until: datetime
    resume_from: datetime
    status: TraceImportStatus
    traces: int
    spans: int
    sample_session: str | None
    error: str | None
    created_by: str
    created_at: datetime
    finished_at: datetime | None


def _lease() -> datetime:
    return datetime.now(UTC) + timedelta(seconds=IMPORT_LEASE_SECONDS)


def _views():
    return (
        select(
            models.TraceImport,
            models.Agent.name,
            models.Workspace.name,
            models.TraceSource.name,
            models.User.username,
        )
        .join(models.Agent, models.Agent.id == models.TraceImport.agent_id)
        .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
        .join(models.TraceSource, models.TraceSource.id == models.TraceImport.trace_source_id)
        .join(models.User, models.User.id == models.TraceImport.created_by)
    )


def _view(
    row: models.TraceImport, agent: str, workspace: str, source: str, user: str
) -> TraceImportView:
    return TraceImportView(
        id=row.id,
        agent=f"{agent}@{workspace}",
        source=source,
        project=row.project,
        since=row.since,
        until=row.until,
        resume_from=row.resume_from,
        status=TraceImportStatus(row.status),
        traces=row.traces,
        spans=row.spans,
        sample_session=row.sample_session,
        error=row.error,
        created_by=user,
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


async def _refuse_second(session: AsyncSession, agent_id: int) -> None:
    await lock_name(session, "trace-import", str(agent_id))
    running = await session.scalar(
        select(models.TraceImport.id).where(
            models.TraceImport.agent_id == agent_id, models.TraceImport.status.in_(ACTIVE)
        )
    )
    if running is not None:
        raise ImportRunning(running)


class TraceImportStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sessions = session_factory

    async def _one(self, session: AsyncSession, agent_id: int, import_id: int) -> TraceImportView:
        found = (
            await session.execute(
                _views().where(
                    models.TraceImport.id == import_id, models.TraceImport.agent_id == agent_id
                )
            )
        ).one_or_none()
        if found is None:
            raise UnknownTraceImport(import_id)
        return _view(*found)

    async def create(
        self,
        agent_id: int,
        trace_source_id: int,
        project: str,
        since: datetime,
        until: datetime,
        created_by: int,
        holder: str,
    ) -> TraceImportView:
        async with self._sessions() as session:
            await _refuse_second(session, agent_id)
            row = models.TraceImport(
                agent_id=agent_id,
                trace_source_id=trace_source_id,
                project=project,
                since=since,
                until=until,
                resume_from=since,
                resume_trace_ids=[],
                status=TraceImportStatus.RUNNING.value,
                traces=0,
                spans=0,
                lease_holder=holder,
                lease_expires_at=_lease(),
                created_by=created_by,
            )
            session.add(row)
            await session.commit()
            return await self._one(session, agent_id, row.id)

    async def get(self, agent_id: int, import_id: int) -> TraceImportView:
        async with self._sessions() as session:
            return await self._one(session, agent_id, import_id)

    async def recent(self, agent_id: int, limit: int = 20) -> list[TraceImportView]:
        async with self._sessions() as session:
            rows = await session.execute(
                _views()
                .where(models.TraceImport.agent_id == agent_id)
                .order_by(models.TraceImport.id.desc())
                .limit(limit)
            )
            return [_view(*row) for row in rows.all()]

    async def stop(self, agent_id: int, import_id: int) -> TraceImportView:
        async with self._sessions() as session:
            current = await self._one(session, agent_id, import_id)
            if current.status is not TraceImportStatus.RUNNING:
                raise ImportNotActive(import_id, current.status, "a running import can be stopped")
            await session.execute(
                update(models.TraceImport)
                .where(
                    models.TraceImport.id == import_id,
                    models.TraceImport.status == TraceImportStatus.RUNNING.value,
                )
                .values(status=TraceImportStatus.STOPPING.value)
            )
            await session.commit()
            return await self._one(session, agent_id, import_id)

    async def restart(self, agent_id: int, import_id: int, holder: str) -> TraceImportView:
        async with self._sessions() as session:
            current = await self._one(session, agent_id, import_id)
            if current.status not in RESTARTABLE:
                raise ImportNotActive(
                    import_id, current.status, "a failed or stopped import can be continued"
                )
            await _refuse_second(session, agent_id)
            await session.execute(
                update(models.TraceImport)
                .where(models.TraceImport.id == import_id)
                .values(
                    status=TraceImportStatus.RUNNING.value,
                    error=None,
                    finished_at=None,
                    lease_holder=holder,
                    lease_expires_at=_lease(),
                )
            )
            await session.commit()
            return await self._one(session, agent_id, import_id)

    async def job(self, import_id: int) -> ImportJob:
        async with self._sessions() as session:
            row = await session.get(models.TraceImport, import_id)
            if row is None:
                raise UnknownTraceImport(import_id)
            return ImportJob(
                id=row.id,
                agent_id=row.agent_id,
                trace_source_id=row.trace_source_id,
                project=row.project,
                until=row.until,
                resume_from=row.resume_from,
                resume_trace_ids=list(row.resume_trace_ids),
            )

    async def advance(
        self,
        import_id: int,
        holder: str,
        *,
        resume_from: datetime,
        resume_trace_ids: list[str],
        traces: int,
        spans: int,
        sample_session: str | None,
    ) -> TraceImportStatus | None:
        async with self._sessions() as session:
            status = await session.scalar(
                update(models.TraceImport)
                .where(
                    models.TraceImport.id == import_id,
                    models.TraceImport.lease_holder == holder,
                    models.TraceImport.status.in_(ACTIVE),
                )
                .values(
                    resume_from=resume_from,
                    resume_trace_ids=resume_trace_ids,
                    traces=models.TraceImport.traces + traces,
                    spans=models.TraceImport.spans + spans,
                    lease_expires_at=_lease(),
                    **({"sample_session": sample_session} if sample_session else {}),
                )
                .returning(models.TraceImport.status)
            )
            await session.commit()
        return TraceImportStatus(status) if status is not None else None

    async def renew(self, import_id: int, holder: str) -> TraceImportStatus | None:
        async with self._sessions() as session:
            status = await session.scalar(
                update(models.TraceImport)
                .where(
                    models.TraceImport.id == import_id,
                    models.TraceImport.lease_holder == holder,
                    models.TraceImport.status.in_(ACTIVE),
                )
                .values(lease_expires_at=_lease())
                .returning(models.TraceImport.status)
            )
            await session.commit()
        return TraceImportStatus(status) if status is not None else None

    async def finish(
        self, import_id: int, holder: str, status: TraceImportStatus, error: str | None = None
    ) -> None:
        async with self._sessions() as session:
            await session.execute(
                update(models.TraceImport)
                .where(
                    models.TraceImport.id == import_id, models.TraceImport.lease_holder == holder
                )
                .values(
                    status=status.value,
                    error=error,
                    finished_at=func.now(),
                    lease_holder=None,
                    lease_expires_at=None,
                )
            )
            await session.commit()


async def reap_imports(session_factory: async_sessionmaker[AsyncSession]) -> list[int]:
    async with session_factory() as session:
        reaped = await session.scalars(
            update(models.TraceImport)
            .where(
                models.TraceImport.status.in_(ACTIVE),
                models.TraceImport.lease_expires_at < datetime.now(UTC),
            )
            .values(
                status=TraceImportStatus.FAILED.value,
                error=IMPORT_STOPPED_ERROR,
                finished_at=func.now(),
                lease_holder=None,
                lease_expires_at=None,
            )
            .returning(models.TraceImport.id)
        )
        ids = list(reaped.all())
        await session.commit()
    return ids
