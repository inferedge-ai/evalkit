from fastapi import APIRouter, Request
from pydantic import BaseModel

from evalkit.api.auth import AdminUser
from evalkit.db.users import UserStore

router = APIRouter(prefix="/api/users", tags=["users"])


def _users(request: Request) -> UserStore:
    return UserStore(request.app.state.session_factory)


@router.get("")
async def list_users(request: Request, admin: AdminUser) -> list[dict]:
    return [
        {
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "archived": u.archived_at is not None,
            "created_at": u.created_at.isoformat(),
        }
        for u in await _users(request).list()
    ]


class CreateUserRequest(BaseModel):
    username: str
    password: str


@router.post("")
async def create_user(body: CreateUserRequest, request: Request, admin: AdminUser) -> dict:
    await _users(request).create(body.username, body.password)
    return {"username": body.username}


@router.post("/{username}/archive")
async def archive_user(username: str, request: Request, admin: AdminUser) -> dict:
    await _users(request).archive(username)
    return {"archived": username}


@router.post("/{username}/restore")
async def restore_user(username: str, request: Request, admin: AdminUser) -> dict:
    await _users(request).restore(username)
    return {"restored": username}


class SetPasswordRequest(BaseModel):
    password: str


@router.post("/{username}/password")
async def set_password(
    username: str, body: SetPasswordRequest, request: Request, admin: AdminUser
) -> dict:
    await _users(request).set_password(username, body.password)
    return {"password_set": username}


@router.post("/{username}/promote")
async def promote_user(username: str, request: Request, admin: AdminUser) -> dict:
    await _users(request).set_admin(username, True)
    return {"promoted": username}


@router.post("/{username}/demote")
async def demote_user(username: str, request: Request, admin: AdminUser) -> dict:
    await _users(request).set_admin(username, False)
    return {"demoted": username}
