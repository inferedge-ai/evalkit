from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.settings import RemoteEvaluatorSettings
from evalkit.core.access import WorkspaceRef
from evalkit.core.remote_evaluators import RemoteEvaluator, UnknownEvaluator
from evalkit.db import models
from evalkit.db.locks import lock_name
from evalkit.db.session import tenant_session
from evalkit.db.workspaces import UnknownWorkspace


class InvalidEvaluator(ValueError):
    pass


class EvaluatorExists(ValueError):
    def __init__(self, ref: str):
        super().__init__(f"evaluator exists: {ref}")


def _view(row: models.RemoteEvaluator, workspace: str) -> RemoteEvaluator:
    return RemoteEvaluator(
        name=row.name,
        workspace=workspace,
        kind=EvaluatorKind(row.kind),
        settings=RemoteEvaluatorSettings.model_validate(row.config),
        archived=row.archived_at is not None,
        has_api_key=row.api_key_encrypted is not None,
    )


class RemoteEvaluatorStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tenant_id: str):
        self._sessions = session_factory
        self._tenant_id = tenant_id

    def _session(self):
        return tenant_session(self._sessions, self._tenant_id)

    async def _row(self, session: AsyncSession, ref: str) -> models.RemoteEvaluator:
        name, _, workspace = ref.partition("@")
        row = await session.scalar(
            select(models.RemoteEvaluator)
            .join(models.Workspace, models.Workspace.id == models.RemoteEvaluator.workspace_id)
            .where(
                models.Workspace.tenant_id == self._tenant_id,
                models.RemoteEvaluator.name == name,
                models.Workspace.name == workspace,
            )
        )
        if row is None:
            raise UnknownEvaluator(ref)
        return row

    async def get(self, ref: str) -> RemoteEvaluator:
        async with self._session() as session:
            return _view(await self._row(session, ref), ref.partition("@")[2])

    async def create(
        self,
        workspace: WorkspaceRef,
        name: str,
        kind: EvaluatorKind,
        settings: RemoteEvaluatorSettings,
        api_key_encrypted: bytes | None = None,
    ) -> RemoteEvaluator:
        if "@" in name or not name:
            raise InvalidEvaluator(f"invalid evaluator name: {name!r}")
        if workspace.tenant_id != self._tenant_id:
            raise UnknownWorkspace(workspace.name)
        async with self._session() as session:
            ws = await session.get_one(models.Workspace, workspace.id)
            await lock_name(session, f"evaluator:{ws.id}", name)
            exists = await session.scalar(
                select(models.RemoteEvaluator.id).where(
                    models.RemoteEvaluator.workspace_id == ws.id,
                    models.RemoteEvaluator.name == name,
                )
            )
            if exists is not None:
                raise EvaluatorExists(f"{name}@{ws.name}")
            row = models.RemoteEvaluator(
                workspace_id=ws.id,
                name=name,
                kind=kind.value,
                config=settings.model_dump(),
                api_key_encrypted=api_key_encrypted,
            )
            session.add(row)
            await session.commit()
            return _view(row, ws.name)

    async def edit(
        self,
        ref: str,
        settings: RemoteEvaluatorSettings | None = None,
        *,
        api_key_encrypted: bytes | None = None,
        clear_api_key: bool = False,
    ) -> RemoteEvaluator:
        async with self._session() as session:
            row = await self._row(session, ref)
            if settings is not None:
                if settings.endpoint != row.config["endpoint"]:
                    row.api_key_encrypted = None
                row.config = settings.model_dump()
            if api_key_encrypted is not None or clear_api_key:
                row.api_key_encrypted = api_key_encrypted
            await session.commit()
            return _view(row, ref.partition("@")[2])

    async def restore(self, ref: str) -> RemoteEvaluator:
        async with self._session() as session:
            row = await self._row(session, ref)
            row.archived_at = None
            await session.commit()
            return _view(row, ref.partition("@")[2])

    async def sealed_keys(self, workspace: str) -> dict[str, tuple[str, bytes]]:
        query = (
            select(
                models.RemoteEvaluator.name,
                models.RemoteEvaluator.config,
                models.RemoteEvaluator.api_key_encrypted,
            )
            .join(models.Workspace, models.Workspace.id == models.RemoteEvaluator.workspace_id)
            .where(
                models.Workspace.tenant_id == self._tenant_id,
                models.Workspace.name == workspace,
                models.RemoteEvaluator.api_key_encrypted.is_not(None),
            )
        )
        async with self._session() as session:
            return {
                name: (config["endpoint"], sealed)
                for name, config, sealed in (await session.execute(query)).all()
            }

    async def archive(self, ref: str) -> RemoteEvaluator:
        async with self._session() as session:
            row = await self._row(session, ref)
            row.archived_at = datetime.now(UTC)
            await session.commit()
            return _view(row, ref.partition("@")[2])

    async def in_workspace(self, workspace: str) -> dict[str, RemoteEvaluator]:
        return {e.name: e for e in await self.list_evaluators(workspace=workspace)}

    async def names(self) -> set[str]:
        return {e.name for e in await self.list_evaluators()}

    async def list_evaluators(
        self, workspace: str | None = None, include_archived: bool = False
    ) -> list[RemoteEvaluator]:
        query = (
            select(models.RemoteEvaluator, models.Workspace.name)
            .join(models.Workspace, models.Workspace.id == models.RemoteEvaluator.workspace_id)
            .where(models.Workspace.tenant_id == self._tenant_id)
            .order_by(models.Workspace.name, models.RemoteEvaluator.name)
        )
        if workspace is not None:
            query = query.where(models.Workspace.name == workspace)
        if not include_archived:
            query = query.where(models.RemoteEvaluator.archived_at.is_(None))
        async with self._session() as session:
            return [_view(row, ws) for row, ws in (await session.execute(query)).all()]
