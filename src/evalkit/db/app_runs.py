from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import RunKind, TrajectorySource
from evalkit.core.app_runs import AppSession
from evalkit.core.traces.mapping import SDK_ROOT_ATTRIBUTE
from evalkit.db import models
from evalkit.db.runs import eval_run_ids, insert_agent_run, publish_agent_run_evaluations
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.spans import is_failed_root
from evalkit.store.base import AgentRunRecord, EvaluationRow


async def agents_with_app_runs(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[tuple[str, int]]:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        rows = await session.execute(
            select(models.Workspace.tenant_id, models.Agent.id)
            .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
            .where(
                models.Agent.trajectory_source == TrajectorySource.OTEL.value,
                models.Agent.archived_at.is_(None),
                models.Workspace.archived_at.is_(None),
            )
            .order_by(models.Agent.id)
        )
        return [(tenant_id, agent_id) for tenant_id, agent_id in rows.all()]


class PgAppRunStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tenant_id: str):
        self._sessions = session_factory
        self._tenant_id = tenant_id

    def _session(self):
        return tenant_session(self._sessions, self._tenant_id)

    async def cursor(self, agent_id: int) -> datetime:
        async with self._session() as session:
            return (
                await session.execute(
                    select(models.Agent.app_runs_stored_until).where(models.Agent.id == agent_id)
                )
            ).scalar_one()

    async def move_cursor(self, agent_id: int, to: datetime) -> None:
        async with self._session() as session:
            await session.execute(
                update(models.Agent)
                .where(models.Agent.id == agent_id)
                .values(app_runs_stored_until=to)
            )
            await session.commit()

    async def list_app_sessions(self, agent_id: int, received_after: datetime) -> list[AppSession]:
        span = models.Span
        touched = (
            select(span.session_id)
            .where(
                span.agent_id == agent_id,
                span.received_at > received_after,
                span.session_id.is_not(None),
                span.session_id.not_in(eval_run_ids(agent_id)),
            )
            .distinct()
        )
        async with self._session() as session:
            rows = (
                await session.execute(
                    select(
                        span.session_id,
                        func.min(span.start_time).label("started"),
                        func.max(span.end_time).label("ended"),
                        func.max(span.received_at).label("received"),
                    )
                    .where(span.agent_id == agent_id, span.session_id.in_(touched))
                    .group_by(span.session_id)
                )
            ).all()
            roots = (
                await session.execute(
                    select(span.session_id, span.name, span.attributes, span.status_code)
                    .where(
                        span.agent_id == agent_id,
                        span.session_id.in_([row.session_id for row in rows]),
                        span.parent_span_id.is_(None),
                    )
                    .order_by(span.start_time)
                )
            ).all()
        titles: dict[str, str] = {}
        failed: set[str] = set()
        from_sdk: set[str] = set()
        for session_id, name, attributes, status_code in roots:
            titles.setdefault(session_id, name)
            if is_failed_root(status_code, attributes):
                failed.add(session_id)
            if attributes.get(SDK_ROOT_ATTRIBUTE):
                from_sdk.add(session_id)
        return [
            AppSession(
                session_id=row.session_id,
                started_at=row.started,
                ended_at=row.ended,
                last_received_at=row.received,
                failed=row.session_id in failed,
                title=titles.get(row.session_id),
                from_sdk_run=row.session_id in from_sdk,
            )
            for row in rows
        ]

    async def stored_run_ids(self, agent_id: int, run_ids: list[str]) -> set[str]:
        if not run_ids:
            return set()
        async with self._session() as session:
            found = await session.scalars(
                select(models.AgentRun.harness_run_id).where(
                    models.AgentRun.agent_id == agent_id,
                    models.AgentRun.harness_run_id.in_(run_ids),
                )
            )
            return set(found.all())

    async def save(self, agent_id: int, run: AgentRunRecord, rows: list[EvaluationRow]) -> bool:
        async with self._session() as session:
            agent_run_id = await insert_agent_run(session, agent_id, run, RunKind.APP)
            if agent_run_id is None:
                return False
            await publish_agent_run_evaluations(session, agent_run_id, rows)
            await session.commit()
            return True
