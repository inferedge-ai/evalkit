import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from evalkit.db.models import User, Workspace, WorkspaceMember


async def test_workspace_type_is_derived(session_factory):
    async with session_factory() as s:
        u = User(username="akshat", password_hash="x")
        s.add(u)
        await s.flush()
        personal = Workspace(name="akshat", owner_user_id=u.id)
        shared = Workspace(name="dev")
        s.add_all([personal, shared])
        await s.commit()
        assert personal.type == "personal" and shared.type == "shared"


async def test_role_check_constraint(session_factory):
    async with session_factory() as s:
        u = User(username="bob", password_hash="x")
        ws = Workspace(name="dev")
        s.add_all([u, ws])
        await s.flush()
        s.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role="owner", granted_by=u.id))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_shared_type_filter_in_sql(session_factory):
    async with session_factory() as s:
        s.add_all([Workspace(name="dev"), Workspace(name="prod")])
        await s.commit()
        names = (
            await s.execute(select(Workspace.name).where(Workspace.type == "shared"))
        ).scalars()
        assert sorted(names) == ["dev", "prod"]
