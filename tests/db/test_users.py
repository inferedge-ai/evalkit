import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from evalkit.contracts.enums import TokenKind
from evalkit.db.agents import AgentStore
from evalkit.db.models import AuthToken
from evalkit.db.users import (
    InvalidName,
    InvalidPassword,
    LastAdmin,
    NameTaken,
    UserStore,
    verify_password,
)
from evalkit.db.workspaces import WorkspaceStore


async def test_create_user_creates_personal_workspace(session_factory):
    await UserStore(session_factory).create("akshat", "password")
    ws = await WorkspaceStore(session_factory).get_by_name("akshat")
    assert ws.owner_user_id is not None and not ws.is_shared


async def test_name_clash_with_shared_workspace(session_factory):
    await WorkspaceStore(session_factory).create_shared("dev", actor_id=None)
    with pytest.raises(NameTaken):
        await UserStore(session_factory).create("dev", "password")


async def test_name_clash_with_user(session_factory):
    await UserStore(session_factory).create("bob", "password")
    with pytest.raises(NameTaken):
        await WorkspaceStore(session_factory).create_shared("bob", actor_id=None)


async def test_archive_revokes_tokens_and_archives_workspace(session_factory):
    users = UserStore(session_factory)
    bob = await users.create("bob", "password")
    async with session_factory() as s:
        s.add(AuthToken(user_id=bob.id, token_hash="h" * 64))
        await s.commit()
    await users.archive("bob")
    ws = await WorkspaceStore(session_factory).get_by_name("bob")
    assert ws.archived
    async with session_factory() as s:
        assert (await s.scalars(select(AuthToken))).all() == []
    await users.restore("bob")
    assert not (await WorkspaceStore(session_factory).get_by_name("bob")).archived


async def test_restore_keeps_a_workspace_archived_on_its_own(session_factory):
    users = UserStore(session_factory)
    await users.create("bob", "password")
    workspaces = WorkspaceStore(session_factory)
    await workspaces.set_archived("bob", datetime.now(UTC) - timedelta(days=1))
    await users.archive("bob")
    await users.restore("bob")
    assert (await workspaces.get_by_name("bob")).archived


async def test_archive_keeps_agent_tokens(session_factory):
    users = UserStore(session_factory)
    await users.create("root", "password", is_admin=True)
    minter = await users.create("minter", "password", is_admin=True)
    await WorkspaceStore(session_factory).create_shared("dev", None)
    agent = await AgentStore(session_factory).create("dev", "ouragent")
    async with session_factory() as s:
        s.add(AuthToken(user_id=minter.id, token_hash="u" * 64))
        s.add(
            AuthToken(
                user_id=minter.id, token_hash="a" * 64, kind=TokenKind.AGENT, agent_id=agent.id
            )
        )
        await s.commit()
    await users.archive("minter")
    async with session_factory() as s:
        kinds = [t.kind for t in (await s.scalars(select(AuthToken))).all()]
    assert kinds == [TokenKind.AGENT]


async def test_set_password_replaces_the_hash_and_signs_out(session_factory):
    users = UserStore(session_factory)
    bob = await users.create("bob", "old-password")
    async with session_factory() as s:
        s.add(AuthToken(user_id=bob.id, token_hash="h" * 64))
        await s.commit()
    await users.set_password("bob", "new-password")
    async with session_factory() as s:
        assert (await s.scalars(select(AuthToken))).all() == []
    [row] = [u for u in await users.list() if u.username == "bob"]
    assert verify_password("new-password", row.password_hash)


@pytest.mark.parametrize("password", ["1234567", "é" * 37])
async def test_create_and_set_password_enforce_the_password_rules(session_factory, password):
    users = UserStore(session_factory)
    with pytest.raises(InvalidPassword):
        await users.create("bob", password)
    await users.create("bob", "12345678")
    with pytest.raises(InvalidPassword):
        await users.set_password("bob", password)
    await users.set_password("bob", "é" * 36)


@pytest.mark.parametrize("name", ["", "a@b", "a/b", "Bob", "two words", "-x", "bob\n"])
async def test_invalid_names_are_refused(session_factory, name):
    with pytest.raises(InvalidName):
        await UserStore(session_factory).create(name, "password")
    with pytest.raises(InvalidName):
        await WorkspaceStore(session_factory).create_shared(name, None)


async def test_concurrent_creates_of_one_name_raise_name_taken(session_factory):
    users = UserStore(session_factory)
    results = await asyncio.gather(
        users.create("twin", "password"),
        users.create("twin", "password"),
        WorkspaceStore(session_factory).create_shared("twin", None),
        return_exceptions=True,
    )
    assert sum(isinstance(r, NameTaken) for r in results) == 2
    assert not [r for r in results if isinstance(r, Exception) and not isinstance(r, NameTaken)]


async def test_concurrent_demotions_keep_one_admin(session_factory):
    users = UserStore(session_factory)
    await users.create("a1", "password", is_admin=True)
    await users.create("a2", "password", is_admin=True)
    results = await asyncio.gather(
        users.set_admin("a1", False), users.set_admin("a2", False), return_exceptions=True
    )
    assert sum(isinstance(r, LastAdmin) for r in results) == 1
    assert sum(u.is_admin for u in await users.list()) == 1


async def test_cannot_demote_or_archive_last_admin(session_factory):
    users = UserStore(session_factory)
    await users.create("root", "password", is_admin=True)
    with pytest.raises(LastAdmin):
        await users.set_admin("root", False)
    with pytest.raises(LastAdmin):
        await users.archive("root")
    await users.create("second", "password")
    await users.set_admin("second", True)
    await users.set_admin("root", False)
