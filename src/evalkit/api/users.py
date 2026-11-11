from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from evalkit.api.access import StoresDep
from evalkit.api.auth import AdminUser

router = APIRouter(prefix="/api/users", tags=["users"])


def _issuer(request: Request) -> str:
    oidc = request.app.state.oidc
    if oidc is None:
        raise HTTPException(status_code=400, detail="SSO is not configured on this server")
    return oidc.cfg.issuer


@router.get("")
async def list_users(stores: StoresDep, admin: AdminUser) -> list[dict]:
    return [
        {
            "id": u.id,
            "username": u.username,
            "is_admin": u.is_admin,
            "archived": u.archived_at is not None,
            "created_at": u.created_at.isoformat(),
            "email": u.email,
            "kind": u.kind,
            "has_subject": u.subject is not None,
        }
        for u in await stores.users.list()
    ]


class CreateUserRequest(BaseModel):
    username: str
    password: str | None = None
    subject: str | None = None


@router.post("")
async def create_user(
    body: CreateUserRequest, request: Request, stores: StoresDep, admin: AdminUser
) -> dict:
    if (body.password is None) == (body.subject is None):
        raise HTTPException(status_code=400, detail="give exactly one of password or subject")
    if body.subject is not None:
        user = await stores.users.create(
            body.username, issuer=_issuer(request), subject=body.subject
        )
    else:
        user = await stores.users.create(body.username, password=body.password)
    return {"username": user.username}


class BindRequest(BaseModel):
    subject: str


@router.post("/{username}/identity")
async def bind_identity(
    username: str, body: BindRequest, request: Request, stores: StoresDep, admin: AdminUser
) -> dict:
    await stores.users.bind(username, _issuer(request), body.subject)
    return {"bound": username, "subject": body.subject}


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
