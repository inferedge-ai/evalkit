from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import ExperimentStatus, TokenKind, TrajectorySource
from evalkit.contracts.esf import MappingDocument
from evalkit.contracts.settings import AgentSettings
from evalkit.core.agents import AgentDefinition, UnknownAgent
from evalkit.core.workspace import Reference, parse_slug
from evalkit.db import models
from evalkit.db.locks import lock_name
from evalkit.db.mappings import activate_document, active_mapping
from evalkit.db.workspaces import workspace_row
from evalkit.store.base import Conflict


async def agent_id_for(session: AsyncSession, ref: str) -> int:
    parsed = parse_slug(ref)
    agent_id = await session.scalar(
        select(models.Agent.id)
        .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
        .where(models.Workspace.name == parsed.workspace, models.Agent.name == parsed.name)
    )
    if agent_id is None:
        raise UnknownAgent(ref)
    return agent_id


class AgentExists(ValueError):
    def __init__(self, ref: str):
        super().__init__(f"agent exists: {ref}")


class InvalidAgent(ValueError):
    pass


def _definition(row: models.Agent, ws: models.Workspace) -> AgentDefinition:
    return AgentDefinition(
        id=row.id,
        name=row.name,
        workspace=ws.name,
        workspace_is_shared=ws.owner_user_id is None,
        harness_url=row.harness_url,
        trajectory_source=TrajectorySource(row.trajectory_source),
        repo_url=row.repo_url,
        settings=AgentSettings.model_validate(row.config or {}),
        archived=row.archived_at is not None,
        has_api_key=row.api_key_encrypted is not None,
    )


def _check_provides(
    harness_url: str | None, source: TrajectorySource, settings: AgentSettings | None
) -> None:
    if harness_url and source is TrajectorySource.ATIF and settings and settings.provides:
        raise InvalidAgent(
            "settings.provides is only allowed next to a harness_url when trajectories come "
            "from traces; a harness declares its own capabilities"
        )


def _ref(ref: str) -> Reference:
    name, sep, workspace = ref.partition("@")
    if not sep or not name or not workspace:
        raise InvalidAgent(f"agent references are name@workspace: {ref!r}")
    return Reference(name, workspace)


class AgentStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sessions = session_factory

    async def _row(self, session: AsyncSession, ref: str) -> tuple[models.Agent, models.Workspace]:
        name, workspace = _ref(ref)
        found = (
            await session.execute(
                select(models.Agent, models.Workspace)
                .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
                .where(models.Agent.name == name, models.Workspace.name == workspace)
            )
        ).one_or_none()
        if found is None:
            raise UnknownAgent(ref)
        return found[0], found[1]

    async def _agent_id(self, session: AsyncSession, name: str, workspace: str) -> int | None:
        return await session.scalar(
            select(models.Agent.id)
            .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
            .where(models.Agent.name == name, models.Workspace.name == workspace)
        )

    async def get(self, ref: str) -> AgentDefinition:
        async with self._sessions() as session:
            return _definition(*await self._row(session, ref))

    async def find(self, ref: str) -> AgentDefinition | None:
        name, workspace = _ref(ref)
        async with self._sessions() as session:
            found = (
                await session.execute(
                    select(models.Agent, models.Workspace)
                    .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
                    .where(models.Agent.name == name, models.Workspace.name == workspace)
                )
            ).one_or_none()
        return _definition(found[0], found[1]) if found is not None else None

    async def get_by_id(self, agent_id: int) -> AgentDefinition:
        async with self._sessions() as session:
            found = (
                await session.execute(
                    select(models.Agent, models.Workspace)
                    .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
                    .where(models.Agent.id == agent_id)
                )
            ).one_or_none()
        if found is None:
            raise UnknownAgent(str(agent_id))
        return _definition(found[0], found[1])

    async def create(
        self,
        workspace: str,
        name: str,
        *,
        harness_url: str | None = None,
        trajectory_source: TrajectorySource = TrajectorySource.ATIF,
        repo_url: str | None = None,
        settings: AgentSettings | None = None,
        created_by: int | None = None,
        mapping: dict | None = None,
        api_key_encrypted: bytes | None = None,
    ) -> AgentDefinition:
        if "@" in name or not name:
            raise InvalidAgent(f"invalid agent name: {name!r}")
        if trajectory_source is TrajectorySource.OTEL and mapping is None:
            raise InvalidAgent("otel agents need a mapping")
        if mapping is not None and trajectory_source is not TrajectorySource.OTEL:
            raise InvalidAgent("only otel agents take a mapping")
        _check_provides(harness_url, trajectory_source, settings)
        if mapping is not None:
            MappingDocument.model_validate(mapping)
        async with self._sessions() as session:
            ws = await workspace_row(session, workspace)
            await lock_name(session, f"agent:{ws.id}", name)
            if await self._agent_id(session, name, workspace) is not None:
                raise AgentExists(f"{name}@{workspace}")
            row = models.Agent(
                workspace_id=ws.id,
                name=name,
                harness_url=harness_url,
                trajectory_source=trajectory_source.value,
                repo_url=repo_url,
                config=(settings or AgentSettings()).model_dump(),
                api_key_encrypted=api_key_encrypted,
                created_by=created_by,
            )
            session.add(row)
            await session.flush()
            if mapping is not None:
                await activate_document(session, row.id, mapping, created_by)
            await session.commit()
            return _definition(row, ws)

    async def edit(
        self,
        ref: str,
        *,
        harness_url: str | None = None,
        repo_url: str | None = None,
        settings: AgentSettings | None = None,
        clear: frozenset[str] = frozenset(),
        trajectory_source: TrajectorySource | None = None,
        mapping: dict | None = None,
        edited_by: int | None = None,
        api_key_encrypted: bytes | None = None,
    ) -> AgentDefinition:
        if mapping is not None:
            MappingDocument.model_validate(mapping)
        async with self._sessions() as session:
            row, ws = await self._row(session, ref)
            if (harness_url is not None or "harness_url" in clear) and (
                harness_url != row.harness_url
            ):
                row.harness_url = harness_url
                row.api_key_encrypted = None
            if api_key_encrypted is not None or "api_key" in clear:
                row.api_key_encrypted = api_key_encrypted
            if repo_url is not None or "repo_url" in clear:
                row.repo_url = repo_url
            if settings is not None:
                row.config = settings.model_dump()
            source = trajectory_source or TrajectorySource(row.trajectory_source)
            if mapping is not None and source is not TrajectorySource.OTEL:
                raise InvalidAgent("only otel agents take a mapping")
            if source.value != row.trajectory_source:
                await self._switch_source(session, row, ref, source, mapping, edited_by)
            elif mapping is not None:
                await activate_document(session, row.id, mapping, edited_by)
            _check_provides(
                row.harness_url,
                TrajectorySource(row.trajectory_source),
                AgentSettings.model_validate(row.config or {}),
            )
            await session.commit()
            return _definition(row, ws)

    async def _switch_source(
        self,
        session: AsyncSession,
        row: models.Agent,
        ref: str,
        source: TrajectorySource,
        mapping: dict | None,
        edited_by: int | None,
    ) -> None:
        active = await session.scalar(
            select(models.Experiment.number)
            .where(
                models.Experiment.agent_id == row.id,
                models.Experiment.status.in_(
                    [ExperimentStatus.RUNNING.value, ExperimentStatus.EVALUATING.value]
                ),
            )
            .limit(1)
        )
        if active is not None:
            raise Conflict(
                f"{ref} has a running experiment; change its trajectory source when it has finished"
            )
        if source is TrajectorySource.OTEL:
            if mapping is not None:
                await activate_document(session, row.id, mapping, edited_by)
            elif await active_mapping(session, row.id) is None:
                raise InvalidAgent(
                    f"{ref} has no active mapping; pass one to switch its trajectory source to otel"
                )
        elif row.harness_url:
            row.config = {**(row.config or {}), "provides": None}
        row.trajectory_source = source.value

    async def sealed_key(self, ref: str) -> bytes | None:
        async with self._sessions() as session:
            row, _ = await self._row(session, ref)
            return row.api_key_encrypted

    async def archive(self, ref: str) -> AgentDefinition:
        async with self._sessions() as session:
            row, ws = await self._row(session, ref)
            row.archived_at = datetime.now(UTC)
            await session.execute(
                delete(models.AuthToken).where(
                    models.AuthToken.agent_id == row.id,
                    models.AuthToken.kind == TokenKind.AGENT.value,
                )
            )
            await session.commit()
            return _definition(row, ws)

    async def restore(self, ref: str) -> AgentDefinition:
        async with self._sessions() as session:
            row, ws = await self._row(session, ref)
            row.archived_at = None
            await session.commit()
            return _definition(row, ws)

    async def list_agents(
        self, workspace: str | None = None, include_archived: bool = False
    ) -> list[AgentDefinition]:
        query = (
            select(models.Agent, models.Workspace)
            .join(models.Workspace, models.Workspace.id == models.Agent.workspace_id)
            .order_by(
                models.Workspace.owner_user_id.is_not(None),
                models.Workspace.name,
                models.Agent.name,
            )
        )
        if workspace is not None:
            query = query.where(models.Workspace.name == workspace)
        if not include_archived:
            query = query.where(models.Agent.archived_at.is_(None))
        async with self._sessions() as session:
            return [_definition(a, w) for a, w in (await session.execute(query)).all()]
