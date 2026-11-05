from evalkit.core.blob import TenantBlobStore, fixture_blob_key, tenant_prefix, trajectory_key
from evalkit.core.tenant import DEFAULT_TENANT_ID
from tests.fake_blob import DictBlobStore


def test_the_default_tenant_keeps_the_unprefixed_layout():
    assert tenant_prefix(DEFAULT_TENANT_ID) == ""
    assert tenant_prefix("ORG-acme") == "tenants/ORG-acme/"


async def test_the_default_tenant_writes_where_evalkit_always_has():
    inner = DictBlobStore()
    await TenantBlobStore(inner, DEFAULT_TENANT_ID).put_object(fixture_blob_key("a" * 64), b"x")
    assert set(inner.objects) == {fixture_blob_key("a" * 64)}


async def test_another_tenant_prefixes_every_key_and_strips_on_list():
    inner = DictBlobStore()
    scoped = TenantBlobStore(inner, "ORG-acme")
    await scoped.put_object(trajectory_key(3, "run-1"), b"{}")
    assert set(inner.objects) == {"tenants/ORG-acme/trajectories/3/run-1.json"}
    assert await scoped.get_object(trajectory_key(3, "run-1")) == b"{}"
    assert await scoped.object_exists(trajectory_key(3, "run-1"))
    assert await scoped.list_objects("trajectories/3/") == [("trajectories/3/run-1.json", 2)]
    assert await scoped.object_url("a") == "memory://blob/tenants/ORG-acme/a"
    await scoped.copy_object(trajectory_key(3, "run-1"), "x")
    assert "tenants/ORG-acme/x" in inner.objects
    await scoped.delete_object("x")
    assert "tenants/ORG-acme/x" not in inner.objects


async def test_two_tenants_do_not_see_each_others_fixtures():
    inner = DictBlobStore()
    sha = "a" * 64
    await TenantBlobStore(inner, DEFAULT_TENANT_ID).put_object(fixture_blob_key(sha), b"one")
    assert not await TenantBlobStore(inner, "ORG-acme").object_exists(fixture_blob_key(sha))
