from __future__ import annotations

import re
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import delete, func, select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import TokenKind
from evalkit.db.locks import lock_name
from evalkit.db.models import AuthToken, User, Workspace
from evalkit.db.session import ALL_TENANTS, tenant_session

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class NameTaken(Exception):
    def __init__(self, name: str):
        super().__init__(f"name {name!r} is already a user or workspace")
        self.name = name


class InvalidName(ValueError):
    def __init__(self, name: str):
        super().__init__(
            f"invalid name {name!r}: use lowercase letters, digits, '.', '_' or '-', "
            "starting with a letter or digit"
        )


def check_name(name: str) -> None:
    if not NAME_PATTERN.fullmatch(name):
        raise InvalidName(name)


async def lock_names(session: AsyncSession) -> None:
    await lock_name(session, "users-and-workspaces")


class IdentityConflict(Exception):
    def __init__(self, message: str):
        super().__init__(message)

    @classmethod
    def name_taken(cls, username: str, subject: str) -> IdentityConflict:
        return cls(
            f"username {username} is taken by a user or workspace; "
            f"ask an admin to bind subject {subject} to an account"
        )

    @classmethod
    def already_bound(cls, subject: str, username: str) -> IdentityConflict:
        return cls(f"subject {subject} is already bound to {username}")


class MachineUser(Exception):
    def __init__(self, username: str):
        super().__init__(f"{username} is a machine user and cannot be an admin")


class UserArchived(Exception):
    def __init__(self, username: str):
        super().__init__("user is archived")
        self.username = username


def derive_username(preferred_username: str | None, email: str) -> str:
    source = preferred_username or email
    username = source.split("@")[0].lower()
    check_name(username)
    return username


class LastAdmin(Exception):
    def __init__(self) -> None:
        super().__init__("cannot remove the last admin")


class UnknownUser(KeyError):
    def __init__(self, username: str):
        super().__init__(f"unknown user: {username}")


class InvalidPassword(ValueError):
    pass


BCRYPT_ROUNDS = 12
PASSWORD_MIN_CHARS = 8
PASSWORD_MAX_BYTES = 72


def check_password_size(password: str) -> None:
    if len(password.encode()) > PASSWORD_MAX_BYTES:
        raise InvalidPassword(f"password is longer than {PASSWORD_MAX_BYTES} bytes")


def check_new_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_CHARS:
        raise InvalidPassword(f"password must be at least {PASSWORD_MIN_CHARS} characters")
    check_password_size(password)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


async def any_admin_exists(session_factory: async_sessionmaker[AsyncSession]) -> bool:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        found = await session.scalar(select(User.id).where(User.is_admin.is_(True)).limit(1))
    return found is not None


async def name_taken(session: AsyncSession, tenant_id: str, name: str) -> bool:
    count = await session.scalar(
        select(func.count()).select_from(
            union_all(
                select(User.id).where(User.tenant_id == tenant_id, User.username == name),
                select(Workspace.id).where(
                    Workspace.tenant_id == tenant_id, Workspace.name == name
                ),
            ).subquery()
        )
    )
    return bool(count)


async def user_id(session: AsyncSession, tenant_id: str, username: str) -> int:
    found = await session.scalar(
        select(User.id).where(User.tenant_id == tenant_id, User.username == username)
    )
    if found is None:
        raise UnknownUser(username)
    return found


class UserStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession], tenant_id: str):
        self._sf = session_factory
        self._tenant_id = tenant_id

    def _session(self):
        return tenant_session(self._sf, self._tenant_id)

    async def create(
        self,
        username: str,
        *,
        password: str | None = None,
        issuer: str | None = None,
        subject: str | None = None,
        email: str | None = None,
        is_admin: bool = False,
    ) -> User:
        check_name(username)
        if (password is None) == (subject is None):
            raise ValueError("give exactly one of password or subject")
        if password is not None:
            check_new_password(password)
        async with self._session() as s:
            await lock_names(s)
            if await name_taken(s, self._tenant_id, username):
                raise NameTaken(username)
            user = User(
                tenant_id=self._tenant_id,
                username=username,
                password_hash=hash_password(password) if password is not None else None,
                issuer=issuer,
                subject=subject,
                email=email,
                is_admin=is_admin,
            )
            s.add(user)
            await s.flush()
            s.add(Workspace(tenant_id=self._tenant_id, name=username, owner_user_id=user.id))
            await s.commit()
            return user

    async def register(
        self, issuer: str, subject: str, email: str, preferred_username: str | None
    ) -> tuple[User, bool]:
        async with self._session() as s:
            await lock_names(s)
            user = await s.scalar(
                select(User).where(
                    User.tenant_id == self._tenant_id,
                    User.issuer == issuer,
                    User.subject == subject,
                )
            )
            if user is not None:
                if user.archived_at is not None:
                    raise UserArchived(user.username)
                if user.email != email:
                    user.email = email
                    await s.commit()
                return user, False
            username = derive_username(preferred_username, email)
            if await name_taken(s, self._tenant_id, username):
                raise IdentityConflict.name_taken(username, subject)
            user = User(
                tenant_id=self._tenant_id,
                username=username,
                password_hash=None,
                issuer=issuer,
                subject=subject,
                email=email,
                is_admin=False,
            )
            s.add(user)
            await s.flush()
            s.add(Workspace(tenant_id=self._tenant_id, name=username, owner_user_id=user.id))
            await s.commit()
            return user, True

    async def bind(self, username: str, issuer: str, subject: str) -> None:
        async with self._session() as s:
            await lock_names(s)
            user = await self._get(s, username)
            other = await s.scalar(
                select(User.username).where(
                    User.tenant_id == self._tenant_id,
                    User.issuer == issuer,
                    User.subject == subject,
                    User.id != user.id,
                )
            )
            if other is not None:
                raise IdentityConflict.already_bound(subject, other)
            user.issuer = issuer
            user.subject = subject
            await s.commit()

    async def _get(self, s: AsyncSession, username: str) -> User:
        user = await s.scalar(
            select(User).where(User.tenant_id == self._tenant_id, User.username == username)
        )
        if user is None:
            raise UnknownUser(username)
        return user

    async def _ensure_other_admin(self, s: AsyncSession, user: User) -> None:
        if not user.is_admin or user.archived_at is not None:
            return
        await lock_name(s, "admins")
        others = await s.scalar(
            select(func.count()).where(
                User.tenant_id == self._tenant_id,
                User.is_admin.is_(True),
                User.archived_at.is_(None),
                User.id != user.id,
            )
        )
        if not others:
            raise LastAdmin()

    async def archive(self, username: str) -> None:
        now = datetime.now(UTC)
        async with self._session() as s:
            user = await self._get(s, username)
            await self._ensure_other_admin(s, user)
            user.archived_at = now
            await s.execute(
                delete(AuthToken).where(
                    AuthToken.user_id == user.id, AuthToken.kind != TokenKind.AGENT.value
                )
            )
            await s.execute(
                update(Workspace)
                .where(Workspace.owner_user_id == user.id, Workspace.archived_at.is_(None))
                .values(archived_at=now)
            )
            await s.commit()

    async def restore(self, username: str) -> None:
        async with self._session() as s:
            user = await self._get(s, username)
            if user.archived_at is not None:
                await s.execute(
                    update(Workspace)
                    .where(
                        Workspace.owner_user_id == user.id,
                        Workspace.archived_at == user.archived_at,
                    )
                    .values(archived_at=None)
                )
            user.archived_at = None
            await s.commit()

    async def set_password(self, username: str, password: str) -> None:
        check_new_password(password)
        async with self._session() as s:
            user = await self._get(s, username)
            user.password_hash = hash_password(password)
            await s.execute(
                delete(AuthToken).where(
                    AuthToken.user_id == user.id, AuthToken.kind != TokenKind.AGENT.value
                )
            )
            await s.commit()

    async def set_admin(self, username: str, value: bool) -> None:
        async with self._session() as s:
            user = await self._get(s, username)
            if not value:
                await self._ensure_other_admin(s, user)
            if value and user.kind == "machine":
                raise MachineUser(username)
            user.is_admin = value
            await s.commit()

    async def list(self) -> list[User]:
        async with self._session() as s:
            return list((await s.scalars(select(User).order_by(User.username))).all())
