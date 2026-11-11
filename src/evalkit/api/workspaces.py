from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from evalkit.api.access import ActorDep, StoresDep, require
from evalkit.api.auth import AdminUser
from evalkit.contracts.enums import WorkspaceRole
from evalkit.core.access import Actor, WorkspaceRef
from evalkit.db.workspaces import WorkspaceStore

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _view(ws: WorkspaceRef, names: dict[int, str], members: dict[int, int]) -> dict:
    return {
        "id": ws.id,
        "tenant_id": ws.tenant_id,
        "name": ws.name,
        "type": "shared" if ws.is_shared else "personal",
        "owner": ws.name if not ws.is_shared else None,
        "archived": ws.archived,
        "promotes_to": names.get(ws.promotes_to) if ws.promotes_to is not None else None,
        "promoted_from": names.get(ws.promoted_from) if ws.promoted_from is not None else None,
        "members": members.get(ws.id, 0),
    }


async def _one(store: WorkspaceStore, ws: WorkspaceRef) -> dict:
    linked = [i for i in (ws.promotes_to, ws.promoted_from) if i is not None]
    names = {i: (await store.get_by_id(i)).name for i in linked}
    return _view(ws, names, await store.count_members())


@router.get("")
async def list_workspaces(stores: StoresDep, include_archived: bool = False) -> list[dict]:
    rows = await stores.workspaces.list(include_archived=True)
    names = {ws.id: ws.name for ws in rows}
    members = await stores.workspaces.count_members()
    return [_view(ws, names, members) for ws in rows if include_archived or not ws.archived]


class CreateWorkspace(BaseModel):
    name: str


@router.post("")
async def create_workspace(body: CreateWorkspace, stores: StoresDep, admin: AdminUser) -> dict:
    store = stores.workspaces
    return await _one(store, await store.create_shared(body.name, admin.id))


@router.get("/{ws}")
async def get_workspace(ws: str, stores: StoresDep) -> dict:
    store = stores.workspaces
    return await _one(store, await store.get_by_name(ws))


class UpdateWorkspace(BaseModel):
    name: str | None = None
    promotes_to: str | None = None


@router.patch("/{ws}")
async def update_workspace(
    ws: str, body: UpdateWorkspace, stores: StoresDep, admin: AdminUser
) -> dict:
    store = stores.workspaces
    update: list[str] = []
    if "promotes_to" in body.model_fields_set:
        await store.set_promotes_to(ws, body.promotes_to)
        if body.promotes_to is not None:
            update.append(
                f"CI that syncs into @{body.promotes_to} must sync into @{ws} instead; "
                f"evalkit sync --workspace {body.promotes_to} now fails"
            )
    if body.name is not None:
        await store.rename(ws, body.name)
        update += [
            f"developer evalkit.yaml credentials keys ending in @{ws}",
            f"CI and scripts that pass --workspace {ws} or refer to name@{ws}",
            f"bookmarked UI links that contain @{ws}",
        ]
    current = await store.get_by_name(body.name or ws)
    return {**(await _one(store, current)), "update": update}


def _may_archive(actor: Actor, ws: WorkspaceRef) -> bool:
    return actor.is_admin if ws.is_shared else ws.owner_user_id == actor.id


@router.post("/{ws}/archive")
async def archive_workspace(ws: str, stores: StoresDep, actor: ActorDep) -> dict:
    store = stores.workspaces
    ref = await store.get_by_name(ws)
    require(_may_archive(actor, ref), "only admins archive shared workspaces, owners their own")
    return await _one(store, await store.set_archived(ws, datetime.now(UTC)))


@router.post("/{ws}/restore")
async def restore_workspace(ws: str, stores: StoresDep, actor: ActorDep) -> dict:
    store = stores.workspaces
    ref = await store.get_by_name(ws)
    require(_may_archive(actor, ref), "only admins restore shared workspaces, owners their own")
    return await _one(store, await store.set_archived(ws, None))


@router.get("/{ws}/members")
async def list_members(ws: str, stores: StoresDep, role: WorkspaceRole | None = None) -> list[dict]:
    return [m.model_dump(mode="json") for m in await stores.workspaces.members(ws, role)]


class AddMember(BaseModel):
    username: str
    role: WorkspaceRole


@router.post("/{ws}/members")
async def add_member(ws: str, body: AddMember, stores: StoresDep, admin: AdminUser) -> dict:
    await stores.workspaces.add_member(ws, body.username, body.role, admin.id)
    return {"workspace": ws, "username": body.username, "role": body.role}


@router.delete("/{ws}/members/{username}/{role}")
async def remove_member(
    ws: str, username: str, role: WorkspaceRole, stores: StoresDep, admin: AdminUser
) -> dict:
    if not await stores.workspaces.remove_member(ws, username, role):
        raise HTTPException(status_code=404, detail=f"{username} is not a {role} of {ws}")
    return {"removed": username, "role": role}
