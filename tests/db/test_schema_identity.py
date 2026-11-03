import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from evalkit.db.models import User, Workspace, WorkspaceMember


async def test_workspace_type_is_derived(session_factory):
    async with session_factory() as s:
        u = User(tenant_id=1, username="akshat", password_hash="x")
        s.add(u)
        await s.flush()
        personal = Workspace(tenant_id=1, name="akshat", owner_user_id=u.id)
        shared = Workspace(tenant_id=1, name="dev")
        s.add_all([personal, shared])
        await s.commit()
        assert personal.type == "personal" and shared.type == "shared"


async def test_role_check_constraint(session_factory):
    async with session_factory() as s:
        u = User(tenant_id=1, username="bob", password_hash="x")
        ws = Workspace(tenant_id=1, name="dev")
        s.add_all([u, ws])
        await s.flush()
        s.add(WorkspaceMember(workspace_id=ws.id, user_id=u.id, role="owner", granted_by=u.id))
        with pytest.raises(IntegrityError):
            await s.commit()


async def test_shared_type_filter_in_sql(session_factory):
    async with session_factory() as s:
        s.add_all([Workspace(tenant_id=1, name="dev"), Workspace(tenant_id=1, name="prod")])
        await s.commit()
        names = (
            await s.execute(select(Workspace.name).where(Workspace.type == "shared"))
        ).scalars()
        assert sorted(names) == ["dev", "prod"]
