from datetime import UTC, datetime

import pytest

from evalkit.contracts.enums import WorkspaceRole
from evalkit.db.models import TenantSettings
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.users import NameTaken, UnknownUser, UserStore
from evalkit.db.workspaces import (
    PromotionLinkError,
    UnknownWorkspace,
    WorkspaceStore,
    _check_link,
)


@pytest.fixture
async def stores(session_factory):
    users = UserStore(session_factory, "default")
    admin = await users.create("admin", password="password", is_admin=True)
    await users.create("bob", password="password")
    await users.create("carol", password="password")
    return WorkspaceStore(session_factory, "default"), admin.id


async def test_create_shared_and_duplicate(stores):
    ws, admin = stores
    dev = await ws.create_shared("dev", admin)
    assert dev.is_shared
    with pytest.raises(NameTaken):
        await ws.create_shared("dev", admin)


async def test_roles_only_on_shared(stores):
    ws, admin = stores
    with pytest.raises(ValueError, match="shared workspaces only"):
        await ws.add_member("bob", "carol", WorkspaceRole.WRITER, admin)


async def test_members_filtered_by_role(stores):
    ws, admin = stores
    await ws.create_shared("dev", admin)
    await ws.add_member("dev", "bob", WorkspaceRole.REVIEWER, admin)
    await ws.add_member("dev", "carol", WorkspaceRole.WRITER, admin)
    await ws.add_member("dev", "carol", WorkspaceRole.WRITER, admin)
    reviewers = await ws.members("dev", WorkspaceRole.REVIEWER)
    assert [(m.username, m.granted_by) for m in reviewers] == [("bob", "admin")]
    assert len(await ws.members("dev")) == 2
    assert await ws.remove_member("dev", "bob", WorkspaceRole.REVIEWER)
    assert await ws.members("dev", WorkspaceRole.REVIEWER) == []


async def test_rename_keeps_id_and_members(stores):
    ws, admin = stores
    dev = await ws.create_shared("dev", admin)
    await ws.add_member("dev", "bob", WorkspaceRole.WRITER, admin)
    renamed = await ws.rename("dev", "staging")
    assert renamed.id == dev.id
    assert [m.username for m in await ws.members("staging")] == ["bob"]
    with pytest.raises(UnknownWorkspace):
        await ws.get_by_name("dev")


async def test_archived_excluded_from_list(stores):
    ws, admin = stores
    await ws.create_shared("dev", admin)
    await ws.set_archived("dev", datetime.now(UTC))
    names = [w.name for w in await ws.list()]
    assert "dev" not in names and "bob" in names
    assert "dev" in [w.name for w in await ws.list(include_archived=True)]


@pytest.fixture
async def linked(stores):
    ws, admin = stores
    for name in ("dev", "prod", "qa"):
        await ws.create_shared(name, admin)
    return ws


async def test_set_and_clear_promotes_to(linked):
    ws = linked
    dev = await ws.set_promotes_to("dev", "prod")
    prod = await ws.get_by_name("prod")
    assert dev.promotes_to == prod.id and not dev.is_target
    assert prod.promoted_from == dev.id and prod.is_target
    listed = {w.name: w for w in await ws.list()}
    assert listed["prod"].promoted_from == dev.id and listed["dev"].promotes_to == prod.id
    assert (await ws.set_promotes_to("dev", None)).promotes_to is None
    assert not (await ws.get_by_name("prod")).is_target


async def test_promotes_to_rules(linked):
    ws = linked
    await ws.set_promotes_to("dev", "prod")
    await ws.set_promotes_to("dev", "prod")
    for source, target in (("qa", "prod"), ("prod", "qa"), ("qa", "dev"), ("dev", "dev")):
        with pytest.raises(PromotionLinkError):
            await ws.set_promotes_to(source, target)
    with pytest.raises(PromotionLinkError):
        await ws.set_promotes_to("qa", "bob")
    await ws.set_archived("qa", datetime.now(UTC))
    await ws.set_promotes_to("dev", None)
    with pytest.raises(PromotionLinkError):
        await ws.set_promotes_to("dev", "qa")


async def test_member_grant_refuses_a_user_from_another_tenant(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(TenantSettings(tenant_id="tenant-2"))
        await s.commit()
    admin = await UserStore(raw_session_factory, "default").create(
        "admin", password="password", is_admin=True
    )
    await WorkspaceStore(raw_session_factory, "default").create_shared("dev", admin.id)
    await UserStore(raw_session_factory, "tenant-2").create("eve", password="password")
    with pytest.raises(UnknownUser):
        await WorkspaceStore(raw_session_factory, "default").add_member(
            "dev", "eve", WorkspaceRole.WRITER, admin.id
        )


async def test_promotion_link_refuses_workspaces_in_different_tenants(raw_session_factory):
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(TenantSettings(tenant_id="tenant-2"))
        await s.commit()
    admin = await UserStore(raw_session_factory, "default").create(
        "admin", password="password", is_admin=True
    )
    root = await UserStore(raw_session_factory, "tenant-2").create(
        "root", password="password", is_admin=True
    )
    dev = await WorkspaceStore(raw_session_factory, "default").create_shared("dev", admin.id)
    prod = await WorkspaceStore(raw_session_factory, "tenant-2").create_shared("prod", root.id)
    with pytest.raises(PromotionLinkError, match="different tenants"):
        _check_link(dev, prod)
