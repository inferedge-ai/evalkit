import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import TokenKind
from evalkit.core.tenant import DEFAULT_TENANT_ID, check_tenant_id
from evalkit.db.auth import find_token
from evalkit.db.models import AuthToken, User, Workspace, WorkspaceMember
from evalkit.db.session import tenant_session
from evalkit.db.tokens import token_hash
from evalkit.db.users import (
    PASSWORD_MAX_BYTES,
    UserStore,
    any_admin_exists,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])

DOWNLOAD_TICKET_TTL = timedelta(seconds=60)


@cache
def _dummy_password_hash() -> str:
    return hash_password(secrets.token_urlsafe(16))


async def issue_token(
    session: AsyncSession, user: User, ttl: timedelta, kind: TokenKind = TokenKind.USER
) -> str:
    token = secrets.token_urlsafe(32)
    session.add(
        AuthToken(
            user_id=user.id,
            token_hash=token_hash(token),
            expires_at=datetime.now(UTC) + ttl,
            kind=kind,
        )
    )
    await session.commit()
    return token


def _bearer(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if not header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return header.removeprefix("Bearer ")


def _check_live(row: AuthToken) -> None:
    expires = token_expiry(row)
    if expires is not None and expires < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="token expired")


async def _active_user(session: AsyncSession, user_id: int) -> User:
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    if user.archived_at is not None:
        raise HTTPException(status_code=401, detail="user is archived")
    return user


async def bearer_login(
    session_factory: async_sessionmaker[AsyncSession], token: str
) -> tuple[AuthToken, User]:
    found = await find_token(session_factory, token_hash(token))
    if found is None:
        raise HTTPException(status_code=401, detail="invalid token")
    row, tenant_id = found
    _check_live(row)
    if row.kind == TokenKind.TICKET:
        raise HTTPException(status_code=401, detail="a download ticket is not a bearer token")
    if row.kind == TokenKind.AGENT:
        raise HTTPException(status_code=403, detail="agent tokens may only export traces")
    async with tenant_session(session_factory, tenant_id) as session:
        await session.execute(
            update(AuthToken).where(AuthToken.id == row.id).values(last_used_at=datetime.now(UTC))
        )
        user = await _active_user(session, row.user_id)
        await session.commit()
    return row, user


def token_expiry(row: AuthToken) -> datetime | None:
    if row.expires_at is None:
        return None
    return row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)


# A download ticket is a single-use token carried in the query string so the
# browser can save a response straight to disk. URLs leak into logs and
# history, so a ticket only ever authorises a GET; a bearer header always wins.
async def current_user(request: Request) -> User:
    session_factory = request.app.state.session_factory
    ticket = request.query_params.get("ticket")
    if ticket is not None and request.method == "GET" and "authorization" not in request.headers:
        found = await find_token(session_factory, token_hash(ticket))
        if found is None:
            raise HTTPException(status_code=401, detail="invalid token")
        row, tenant_id = found
        _check_live(row)
        if row.kind != TokenKind.TICKET:
            raise HTTPException(status_code=401, detail="not a download ticket")
        async with tenant_session(session_factory, tenant_id) as session:
            await session.execute(delete(AuthToken).where(AuthToken.id == row.id))
            user = await _active_user(session, row.user_id)
            await session.commit()
        return user
    row, user = await bearer_login(session_factory, _bearer(request))
    request.state.token_expires_at = token_expiry(row)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@dataclass(frozen=True)
class Principal:
    user: User | None
    agent_id: int | None
    tenant_id: str


async def collector_principal(request: Request) -> Principal:
    session_factory = request.app.state.session_factory
    found = await find_token(session_factory, token_hash(_bearer(request)))
    if found is None:
        raise HTTPException(status_code=401, detail="invalid token")
    row, tenant_id = found
    _check_live(row)
    if row.kind == TokenKind.TICKET:
        raise HTTPException(status_code=401, detail="a download ticket is not a bearer token")
    async with tenant_session(session_factory, tenant_id) as session:
        await session.execute(
            update(AuthToken).where(AuthToken.id == row.id).values(last_used_at=datetime.now(UTC))
        )
        if row.kind == TokenKind.AGENT:
            await session.commit()
            return Principal(user=None, agent_id=row.agent_id, tenant_id=tenant_id)
        user = await _active_user(session, row.user_id)
        await session.commit()
    return Principal(user=user, agent_id=None, tenant_id=tenant_id)


CollectorPrincipal = Annotated[Principal, Depends(collector_principal)]


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login")
async def login(body: LoginRequest, request: Request) -> dict:
    tenant_id = check_tenant_id(request.app.state.resolve_tenant(request, None))
    if len(body.password.encode()) > PASSWORD_MAX_BYTES:
        raise HTTPException(
            status_code=400, detail=f"password is longer than {PASSWORD_MAX_BYTES} bytes"
        )
    async with tenant_session(request.app.state.session_factory, tenant_id) as session:
        user = (
            await session.execute(
                select(User).where(User.tenant_id == tenant_id, User.username == body.username)
            )
        ).scalar_one_or_none()
        if user is None or user.password_hash is None:
            verify_password(body.password, _dummy_password_hash())
            raise HTTPException(status_code=401, detail="invalid username or password")
        if not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="invalid username or password")
        if user.archived_at is not None:
            raise HTTPException(status_code=401, detail="user is archived")
        ttl = timedelta(hours=request.app.state.cfg.auth.token_ttl_hours)
        token = await issue_token(session, user, ttl)
    return {"token": token, "username": user.username, "is_admin": user.is_admin}


@router.post("/download-ticket")
async def download_ticket(request: Request, user: CurrentUser) -> dict:
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        await session.execute(delete(AuthToken).where(AuthToken.expires_at < datetime.now(UTC)))
        ticket = await issue_token(session, user, DOWNLOAD_TICKET_TTL, TokenKind.TICKET)
    return {"ticket": ticket}


@router.post("/logout")
async def logout(request: Request, user: CurrentUser) -> dict:
    token = _bearer(request)
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        await session.execute(delete(AuthToken).where(AuthToken.token_hash == token_hash(token)))
        await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(request: Request, user: CurrentUser) -> dict:
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        rows = (
            await session.execute(
                select(Workspace.name, WorkspaceMember.role)
                .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
                .where(WorkspaceMember.user_id == user.id)
                .order_by(Workspace.name, WorkspaceMember.role)
            )
        ).all()
        personal = await session.scalar(
            select(Workspace.name).where(
                Workspace.owner_user_id == user.id, Workspace.name == user.username
            )
        )
    roles: dict[str, list[str]] = {}
    for name, role in rows:
        roles.setdefault(name, []).append(role)
    return {
        "username": user.username,
        "is_admin": user.is_admin,
        "personal_workspace": personal,
        "roles": roles,
        "token_expires_at": getattr(request.state, "token_expires_at", None),
    }


async def bootstrap_admin(
    session_factory: async_sessionmaker[AsyncSession], password: str | None
) -> None:
    if await any_admin_exists(session_factory):
        return
    if not password:
        raise RuntimeError(
            "no admin user exists and EVALKIT_ADMIN_PASSWORD is not set; "
            "set it and start the server again to create the admin user"
        )
    await UserStore(session_factory, DEFAULT_TENANT_ID).create("admin", password, is_admin=True)
