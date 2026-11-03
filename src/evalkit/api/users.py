from fastapi import APIRouter
from pydantic import BaseModel

from evalkit.api.access import StoresDep
from evalkit.api.auth import AdminUser

router = APIRouter(prefix="/api/users", tags=["users"])


@router.get("")
async def list_users(stores: StoresDep, admin: AdminUser) -> list[dict]:
    return [
        {
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "archived": u.archived_at is not None,
            "created_at": u.created_at.isoformat(),
        }
        for u in await stores.users.list()
    ]


class CreateUserRequest(BaseModel):
    username: str
    password: str


@router.post("")
async def create_user(body: CreateUserRequest, stores: StoresDep, admin: AdminUser) -> dict:
    await stores.users.create(body.username, body.password)
    return {"username": body.username}


@router.post("/{username}/archive")
async def archive_user(username: str, stores: StoresDep, admin: AdminUser) -> dict:
    await stores.users.archive(username)
    return {"archived": username}


@router.post("/{username}/restore")
async def restore_user(username: str, stores: StoresDep, admin: AdminUser) -> dict:
    await stores.users.restore(username)
    return {"restored": username}


class SetPasswordRequest(BaseModel):
    password: str


@router.post("/{username}/password")
async def set_password(
    username: str, body: SetPasswordRequest, stores: StoresDep, admin: AdminUser
) -> dict:
    await stores.users.set_password(username, body.password)
    return {"password_set": username}


@router.post("/{username}/promote")
async def promote_user(username: str, stores: StoresDep, admin: AdminUser) -> dict:
    await stores.users.set_admin(username, True)
    return {"promoted": username}


@router.post("/{username}/demote")
async def demote_user(username: str, stores: StoresDep, admin: AdminUser) -> dict:
    await stores.users.set_admin(username, False)
    return {"demoted": username}
