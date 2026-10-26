import re
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import delete, func, select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import TokenKind
from evalkit.db.locks import lock_name
from evalkit.db.models import AuthToken, User, Workspace

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


async def name_taken(session: AsyncSession, name: str) -> bool:
    count = await session.scalar(
        select(func.count()).select_from(
            union_all(
                select(User.id).where(User.username == name),
                select(Workspace.id).where(Workspace.name == name),
            ).subquery()
        )
    )
    return bool(count)


async def user_id(session: AsyncSession, username: str) -> int:
    found = await session.scalar(select(User.id).where(User.username == username))
    if found is None:
        raise UnknownUser(username)
    return found


class UserStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def create(self, username: str, password: str, *, is_admin: bool = False) -> User:
        check_name(username)
        check_new_password(password)
        async with self._sf() as s:
            await lock_names(s)
            if await name_taken(s, username):
                raise NameTaken(username)
            user = User(username=username, password_hash=hash_password(password), is_admin=is_admin)
            s.add(user)
            await s.flush()
            s.add(Workspace(name=username, owner_user_id=user.id))
            await s.commit()
            return user

    async def _get(self, s: AsyncSession, username: str) -> User:
        user = await s.scalar(select(User).where(User.username == username))
        if user is None:
            raise UnknownUser(username)
        return user

    async def _ensure_other_admin(self, s: AsyncSession, user: User) -> None:
        if not user.is_admin or user.archived_at is not None:
            return
        await lock_name(s, "admins")
        others = await s.scalar(
            select(func.count()).where(
                User.is_admin.is_(True), User.archived_at.is_(None), User.id != user.id
            )
        )
        if not others:
            raise LastAdmin()

    async def archive(self, username: str) -> None:
        now = datetime.now(UTC)
        async with self._sf() as s:
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
        async with self._sf() as s:
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
        async with self._sf() as s:
            user = await self._get(s, username)
            user.password_hash = hash_password(password)
            await s.execute(
                delete(AuthToken).where(
                    AuthToken.user_id == user.id, AuthToken.kind != TokenKind.AGENT.value
                )
            )
            await s.commit()

    async def set_admin(self, username: str, value: bool) -> None:
        async with self._sf() as s:
            user = await self._get(s, username)
            if not value:
                await self._ensure_other_admin(s, user)
            user.is_admin = value
            await s.commit()

    async def list(self) -> list[User]:
        async with self._sf() as s:
            return list((await s.scalars(select(User).order_by(User.username))).all())
