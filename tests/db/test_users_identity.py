import pytest

from evalkit.db.users import (
    IdentityConflict,
    InvalidName,
    UserArchived,
    UserStore,
    derive_username,
)
from evalkit.db.workspaces import WorkspaceStore

ISSUER = "https://idp.test"


@pytest.mark.parametrize(
    "preferred,email,expected",
    [
        ("ann", "ann@example.com", "ann"),
        ("Ann.Lee@Example.com", "x@example.com", "ann.lee"),
        (None, "Bob_Smith@example.com", "bob_smith"),
        ("okta|ann", "ann@example.com", None),
    ],
)
def test_derive_username(preferred, email, expected):
    if expected is None:
        with pytest.raises(InvalidName):
            derive_username(preferred, email)
    else:
        assert derive_username(preferred, email) == expected


async def test_create_takes_a_password_or_a_subject(session_factory):
    store = UserStore(session_factory, 1)
    with pytest.raises(ValueError, match="password or subject"):
        await store.create("x")
    with pytest.raises(ValueError, match="password or subject"):
        await store.create("x", password="hunter2-pw", subject="s")
    machine = await store.create("ci-bot", issuer=ISSUER, subject="client-1")
    assert machine.password_hash is None and machine.email is None and machine.kind == "machine"
    person = await store.create("ann", password="hunter2-pw")
    assert person.subject is None and person.kind == "human"


async def test_register_creates_once_and_refreshes_email(session_factory):
    store = UserStore(session_factory, 1)
    user, created = await store.register(ISSUER, "sub-1", "ann@example.com", "ann")
    assert created and user.username == "ann" and user.kind == "human"
    again, created = await store.register(ISSUER, "sub-1", "ann@new.example.com", "ann")
    assert not created and again.id == user.id and again.email == "ann@new.example.com"
    workspaces = [w.name for w in await WorkspaceStore(session_factory, 1).list()]
    assert "ann" in workspaces


async def test_register_refuses_a_taken_name_and_an_archived_user(session_factory):
    store = UserStore(session_factory, 1)
    await store.create("ann", password="hunter2-pw")
    with pytest.raises(IdentityConflict, match="taken by a user or workspace"):
        await store.register(ISSUER, "sub-2", "ann@example.com", "ann")
    user, _ = await store.register(ISSUER, "sub-3", "cy@example.com", "cy")
    await store.archive("cy")
    with pytest.raises(UserArchived):
        await store.register(ISSUER, "sub-3", "cy@example.com", "cy")
