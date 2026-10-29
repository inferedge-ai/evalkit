from typing import Annotated

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.api.auth import CurrentUser
from evalkit.api.deps import SessionDep
from evalkit.config import Config
from evalkit.contracts.enums import WorkspaceRole
from evalkit.core.access import Actor, WorkspaceRef, can_write
from evalkit.core.endpoints import check_endpoint
from evalkit.db.models import User, WorkspaceMember
from evalkit.db.workspaces import load_ref, workspace_row


async def load_actor(session: AsyncSession, user: User) -> Actor:
    rows = (
        await session.execute(
            select(WorkspaceMember.workspace_id, WorkspaceMember.role).where(
                WorkspaceMember.user_id == user.id
            )
        )
    ).all()
    roles: dict[int, set[WorkspaceRole]] = {}
    for ws_id, role in rows:
        roles.setdefault(ws_id, set()).add(WorkspaceRole(role))
    return Actor(user.id, user.username, user.is_admin, {k: frozenset(v) for k, v in roles.items()})


async def current_actor(session: SessionDep, user: CurrentUser) -> Actor:
    return await load_actor(session, user)


ActorDep = Annotated[Actor, Depends(current_actor)]


async def workspace_ref(session: AsyncSession, name: str) -> WorkspaceRef:
    return await load_ref(session, await workspace_row(session, name))


def require(allowed: bool, detail: str) -> None:
    if not allowed:
        raise HTTPException(status_code=403, detail=detail)


def ensure_active(ws: WorkspaceRef) -> None:
    if ws.archived:
        raise HTTPException(status_code=409, detail=f"workspace {ws.name} is archived")


PERSONAL_KEY = "personal agents and evaluators read their key from evalkit.yaml"


async def writable_endpoint(
    session: SessionDep,
    actor: ActorDep,
    cfg: Config,
    workspace: str,
    *,
    api_key: bool = False,
    url: str | None = None,
) -> None:
    ws = await workspace_ref(session, workspace)
    require(can_write(actor, ws), f"you cannot write to @{workspace}")
    ensure_active(ws)
    if api_key and not ws.is_shared:
        raise HTTPException(status_code=400, detail=PERSONAL_KEY)
    if url is not None and ws.is_shared:
        await check_endpoint(url, allow_private=cfg.allow_private_endpoints)


async def writable(session: SessionDep, actor: ActorDep, workspace: str) -> None:
    ws = await workspace_ref(session, workspace)
    require(can_write(actor, ws), f"you cannot write to @{workspace}")
    ensure_active(ws)
