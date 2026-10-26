import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.api.deps import SessionDep
from evalkit.contracts.enums import TokenKind
from evalkit.db.models import AuthToken, User, Workspace, WorkspaceMember
from evalkit.db.tokens import token_hash
from evalkit.db.users import PASSWORD_MAX_BYTES, UserStore, hash_password, verify_password

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


async def _valid_token(session: AsyncSession, token: str) -> AuthToken:
    row = (
        await session.execute(select(AuthToken).where(AuthToken.token_hash == token_hash(token)))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=401, detail="invalid token")
    expires = token_expiry(row)
    if expires is not None and expires < datetime.now(UTC):
        raise HTTPException(status_code=401, detail="token expired")
    return row


async def _valid_bearer_token(session: AsyncSession, token: str) -> AuthToken:
    row = await _valid_token(session, token)
    if row.kind == TokenKind.TICKET:
        raise HTTPException(status_code=401, detail="a download ticket is not a bearer token")
    row.last_used_at = datetime.now(UTC)
    return row


async def _valid_bearer(request: Request, session: AsyncSession) -> AuthToken:
    return await _valid_bearer_token(session, _bearer(request))


# A person's bearer token, by the rules every non-collector route applies; /mcp
# calls it too, so the two surfaces accept exactly the same tokens.
async def bearer_login(session: AsyncSession, token: str) -> tuple[AuthToken, User]:
    row = await _valid_bearer_token(session, token)
    if row.kind == TokenKind.AGENT:
        raise HTTPException(status_code=403, detail="agent tokens may only export traces")
    return row, await _active_user(session, row.user_id)


def token_expiry(row: AuthToken) -> datetime | None:
    if row.expires_at is None:
        return None
    return row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)


# A download ticket is a single-use token carried in the query string so the
# browser can save a response straight to disk. URLs leak into logs and
# history, so a ticket only ever authorises a GET; a bearer header always wins.
async def current_user(request: Request, session: SessionDep) -> User:
    ticket = request.query_params.get("ticket")
    if ticket is not None and request.method == "GET" and "authorization" not in request.headers:
        row = await _valid_token(session, ticket)
        if row.kind != TokenKind.TICKET:
            raise HTTPException(status_code=401, detail="not a download ticket")
        await session.delete(row)
        user = await _active_user(session, row.user_id)
    else:
        row, user = await bearer_login(session, _bearer(request))
        request.state.token_expires_at = token_expiry(row)
    await session.commit()
    return user


async def _active_user(session: AsyncSession, user_id: int) -> User:
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
    if user.archived_at is not None:
        raise HTTPException(status_code=401, detail="user is archived")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


@dataclass(frozen=True)
class Principal:
    user: User | None
    agent_id: int | None


async def collector_principal(request: Request, session: SessionDep) -> Principal:
    row = await _valid_bearer(request, session)
    if row.kind == TokenKind.AGENT:
        await session.commit()
        return Principal(user=None, agent_id=row.agent_id)
    user = await _active_user(session, row.user_id)
    await session.commit()
    return Principal(user=user, agent_id=None)


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
async def login(body: LoginRequest, request: Request, session: SessionDep) -> dict:
    user = (
        await session.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if len(body.password.encode()) > PASSWORD_MAX_BYTES:
        raise HTTPException(
            status_code=400, detail=f"password is longer than {PASSWORD_MAX_BYTES} bytes"
        )
    if user is None:
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
async def download_ticket(session: SessionDep, user: CurrentUser) -> dict:
    await session.execute(delete(AuthToken).where(AuthToken.expires_at < datetime.now(UTC)))
    return {"ticket": await issue_token(session, user, DOWNLOAD_TICKET_TTL, TokenKind.TICKET)}


@router.post("/logout")
async def logout(request: Request, session: SessionDep, user: CurrentUser) -> dict:
    token = _bearer(request)
    await session.execute(delete(AuthToken).where(AuthToken.token_hash == token_hash(token)))
    await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(request: Request, session: SessionDep, user: CurrentUser) -> dict:
    rows = (
        await session.execute(
            select(Workspace.name, WorkspaceMember.role)
            .join(Workspace, Workspace.id == WorkspaceMember.workspace_id)
            .where(WorkspaceMember.user_id == user.id)
            .order_by(Workspace.name, WorkspaceMember.role)
        )
    ).all()
    roles: dict[str, list[str]] = {}
    for name, role in rows:
        roles.setdefault(name, []).append(role)
    personal = await session.scalar(
        select(Workspace.name).where(
            Workspace.owner_user_id == user.id, Workspace.name == user.username
        )
    )
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
    async with session_factory() as session:
        existing = await session.scalar(select(User.id).where(User.is_admin.is_(True)))
    if existing is not None:
        return
    if not password:
        raise RuntimeError(
            "no admin user exists and EVALKIT_ADMIN_PASSWORD is not set; "
            "set it and start the server again to create the admin user"
        )
    await UserStore(session_factory).create("admin", password, is_admin=True)
