from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from evalkit.contracts.enums import WorkspaceRole
from evalkit.core.access import WorkspaceRef
from evalkit.db.models import User, Workspace, WorkspaceMember, WorkspacePromotion
from evalkit.db.users import NameTaken, check_name, lock_names, name_taken, user_id


class UnknownWorkspace(KeyError):
    def __init__(self, name: str):
        super().__init__(f"unknown workspace: {name}")


class SharedOnly(ValueError):
    pass


class PromotionLinkError(ValueError):
    pass


class MemberRow(BaseModel):
    username: str
    role: WorkspaceRole
    granted_by: str
    created_at: datetime


def to_ref(
    row: Workspace, promotes_to: int | None = None, promoted_from: int | None = None
) -> WorkspaceRef:
    return WorkspaceRef(
        row.id,
        row.name,
        row.owner_user_id,
        row.archived_at is not None,
        promotes_to=promotes_to,
        promoted_from=promoted_from,
    )


async def workspace_row(session: AsyncSession, name: str) -> Workspace:
    row = await session.scalar(select(Workspace).where(Workspace.name == name))
    if row is None:
        raise UnknownWorkspace(name)
    return row


async def _links(session: AsyncSession, workspace_id: int) -> tuple[int | None, int | None]:
    rows = (
        await session.execute(
            select(WorkspacePromotion.source_id, WorkspacePromotion.target_id).where(
                (WorkspacePromotion.source_id == workspace_id)
                | (WorkspacePromotion.target_id == workspace_id)
            )
        )
    ).all()
    promotes_to = next((t for s, t in rows if s == workspace_id), None)
    promoted_from = next((s for s, t in rows if t == workspace_id), None)
    return promotes_to, promoted_from


async def load_ref(session: AsyncSession, row: Workspace) -> WorkspaceRef:
    promotes_to, promoted_from = await _links(session, row.id)
    return to_ref(row, promotes_to=promotes_to, promoted_from=promoted_from)


def _check_link(source: WorkspaceRef, target: WorkspaceRef) -> None:
    if source.id == target.id:
        raise PromotionLinkError(f"{source.name} cannot promote to itself")
    for ws in (source, target):
        if not ws.is_shared:
            raise PromotionLinkError(
                f"{ws.name} is a personal workspace; promotion links shared ones"
            )
        if ws.archived:
            raise PromotionLinkError(f"{ws.name} is archived")
    if source.is_target:
        raise PromotionLinkError(f"{source.name} is a promotion target; chains are not supported")
    if target.promoted_from is not None and target.promoted_from != source.id:
        raise PromotionLinkError(
            f"{target.name} is already the promotion target of another workspace"
        )
    if target.promotes_to is not None:
        raise PromotionLinkError(f"{target.name} is a promotion source; chains are not supported")


class WorkspaceStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def get_by_name(self, name: str) -> WorkspaceRef:
        async with self._sf() as s:
            return await load_ref(s, await workspace_row(s, name))

    async def get_by_id(self, workspace_id: int) -> WorkspaceRef:
        async with self._sf() as s:
            row = await s.get(Workspace, workspace_id)
            if row is None:
                raise UnknownWorkspace(str(workspace_id))
            return await load_ref(s, row)

    async def set_promotes_to(self, source: str, target: str | None) -> WorkspaceRef:
        async with self._sf() as s:
            await lock_names(s)
            row = await workspace_row(s, source)
            await s.execute(
                delete(WorkspacePromotion).where(WorkspacePromotion.source_id == row.id)
            )
            if target is not None:
                source_ref = to_ref(row, promoted_from=(await _links(s, row.id))[1])
                target_ref = await load_ref(s, await workspace_row(s, target))
                _check_link(source_ref, target_ref)
                s.add(WorkspacePromotion(source_id=row.id, target_id=target_ref.id))
            await s.commit()
            return await load_ref(s, row)

    async def personal_of(self, username: str) -> WorkspaceRef:
        async with self._sf() as s:
            row = await s.scalar(
                select(Workspace)
                .join(User, User.id == Workspace.owner_user_id)
                .where(User.username == username, Workspace.name == username)
            )
            if row is None:
                raise UnknownWorkspace(username)
            return to_ref(row)

    async def create_shared(self, name: str, actor_id: int | None) -> WorkspaceRef:
        check_name(name)
        async with self._sf() as s:
            await lock_names(s)
            if await name_taken(s, name):
                raise NameTaken(name)
            row = Workspace(name=name)
            s.add(row)
            await s.commit()
            return to_ref(row)

    async def rename(self, name: str, new_name: str) -> WorkspaceRef:
        check_name(new_name)
        async with self._sf() as s:
            await lock_names(s)
            row = await workspace_row(s, name)
            if row.owner_user_id is not None:
                raise SharedOnly("personal workspaces keep their owner's name")
            if await name_taken(s, new_name):
                raise NameTaken(new_name)
            row.name = new_name
            await s.commit()
            return to_ref(row)

    async def set_archived(self, name: str, archived_at: datetime | None) -> WorkspaceRef:
        async with self._sf() as s:
            row = await workspace_row(s, name)
            row.archived_at = archived_at
            await s.commit()
            return to_ref(row)

    async def add_member(
        self, ws: str, username: str, role: WorkspaceRole, granted_by: int
    ) -> None:
        async with self._sf() as s:
            row = await workspace_row(s, ws)
            if row.owner_user_id is not None:
                raise SharedOnly("roles apply to shared workspaces only")
            uid = await user_id(s, username)
            await s.execute(
                insert(WorkspaceMember)
                .values(workspace_id=row.id, user_id=uid, role=role.value, granted_by=granted_by)
                .on_conflict_do_nothing()
            )
            await s.commit()

    async def remove_member(self, ws: str, username: str, role: WorkspaceRole) -> bool:
        async with self._sf() as s:
            row = await workspace_row(s, ws)
            uid = await user_id(s, username)
            result = await s.execute(
                delete(WorkspaceMember).where(
                    WorkspaceMember.workspace_id == row.id,
                    WorkspaceMember.user_id == uid,
                    WorkspaceMember.role == role.value,
                )
            )
            await s.commit()
            return bool(getattr(result, "rowcount", 0))

    async def members(self, ws: str, role: WorkspaceRole | None = None) -> list[MemberRow]:
        member, granter = aliased(User), aliased(User)
        async with self._sf() as s:
            row = await workspace_row(s, ws)
            query = (
                select(
                    member.username,
                    WorkspaceMember.role,
                    granter.username,
                    WorkspaceMember.created_at,
                )
                .join(member, member.id == WorkspaceMember.user_id)
                .join(granter, granter.id == WorkspaceMember.granted_by)
                .where(WorkspaceMember.workspace_id == row.id)
                .order_by(member.username, WorkspaceMember.role)
            )
            if role is not None:
                query = query.where(WorkspaceMember.role == role.value)
            return [
                MemberRow(username=u, role=WorkspaceRole(r), granted_by=g, created_at=c)
                for u, r, g, c in (await s.execute(query)).all()
            ]

    async def list(self, include_archived: bool = False) -> list[WorkspaceRef]:
        query = select(Workspace).order_by(Workspace.name)
        if not include_archived:
            query = query.where(Workspace.archived_at.is_(None))
        async with self._sf() as s:
            rows = (await s.scalars(query)).all()
            links = (
                await s.execute(select(WorkspacePromotion.source_id, WorkspacePromotion.target_id))
            ).all()
            targets = {source: target for source, target in links}
            sources = {target: source for source, target in links}
            return [
                to_ref(row, promotes_to=targets.get(row.id), promoted_from=sources.get(row.id))
                for row in rows
            ]
