import pytest
from sqlalchemy import select, text, update

from evalkit.db.models import Tenant, User, Workspace
from evalkit.db.session import ALL_TENANTS, tenant_session


async def _add_tenant_2(raw_session_factory) -> None:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(Tenant(id=2, name="other"))
        s.add(User(tenant_id=2, username="eve", password_hash="x"))
        s.add(User(tenant_id=1, username="akshat", password_hash="x"))
        await s.commit()


async def test_a_session_without_a_tenant_fails_its_first_query(raw_session_factory):
    async with raw_session_factory() as s:
        with pytest.raises(RuntimeError, match="session has no tenant"):
            await s.execute(select(User))


async def test_a_scoped_session_cannot_read_a_foreign_root_row(raw_session_factory):
    await _add_tenant_2(raw_session_factory)
    async with tenant_session(raw_session_factory, 1) as s:
        names = (await s.scalars(select(User.username))).all()
        assert names == ["akshat"]
        eve_id = None
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        eve_id = await s.scalar(select(User.id).where(User.username == "eve"))
    async with tenant_session(raw_session_factory, 1) as s:
        assert await s.get(User, eve_id) is None


async def test_a_scoped_session_cannot_update_a_foreign_root_row(raw_session_factory):
    await _add_tenant_2(raw_session_factory)
    async with tenant_session(raw_session_factory, 1) as s:
        await s.execute(update(User).values(is_admin=True))
        await s.commit()
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        admins = (await s.scalars(select(User.username).where(User.is_admin.is_(True)))).all()
        assert admins == ["akshat"]


async def test_all_tenants_reads_everything(raw_session_factory):
    await _add_tenant_2(raw_session_factory)
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        assert set((await s.scalars(select(User.username))).all()) == {"akshat", "eve"}


async def test_core_statements_are_untouched(raw_session_factory):
    async with tenant_session(raw_session_factory, 1) as s:
        assert await s.scalar(text("SELECT 1")) == 1


async def test_the_default_test_factory_is_tenant_1(session_factory):
    async with session_factory() as s:
        assert s.info["tenant_id"] == 1
        s.add(Workspace(tenant_id=1, name="dev"))
        await s.commit()
