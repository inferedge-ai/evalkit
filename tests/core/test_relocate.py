from evalkit.core.blob import TenantBlobStore, fixture_blob_key, tenant_prefix, trajectory_key
from evalkit.core.relocate import old_prefix_objects_remain, relocate_blobs
from tests.fake_blob import DictBlobStore


async def test_tenant_blob_store_prefixes_every_key_and_strips_on_list():
    inner = DictBlobStore()
    scoped = TenantBlobStore(inner, 7)
    await scoped.put_object(trajectory_key(3, "run-1"), b"{}")
    assert set(inner.objects) == {"tenants/7/trajectories/3/run-1.json"}
    assert await scoped.get_object(trajectory_key(3, "run-1")) == b"{}"
    assert await scoped.object_exists(trajectory_key(3, "run-1"))
    assert await scoped.list_objects("trajectories/3/") == [("trajectories/3/run-1.json", 2)]
    assert await scoped.has_any("trajectories/")
    assert not await scoped.has_any("artifacts/")
    assert await scoped.object_url("a") == "memory://blob/tenants/7/a"
    await scoped.copy_object(trajectory_key(3, "run-1"), "x")
    assert "tenants/7/x" in inner.objects
    await scoped.delete_object("x")
    assert "tenants/7/x" not in inner.objects


async def test_two_tenants_do_not_see_each_others_fixtures():
    inner = DictBlobStore()
    sha = "a" * 64
    await TenantBlobStore(inner, 1).put_object(fixture_blob_key(sha), b"one")
    assert not await TenantBlobStore(inner, 2).object_exists(fixture_blob_key(sha))


async def test_relocate_moves_old_layout_under_tenant_1_and_is_idempotent():
    inner = DictBlobStore()
    await inner.put_object("fixtures/sha256/aa", b"f")
    await inner.put_object("trajectories/1/r.json", b"t")
    await inner.put_object("artifacts/1/r/out.md", b"a")
    await inner.put_object("workspaces/1/r.json", b"w")
    await inner.put_object("tenants/1/trajectories/1/already.json", b"x")
    assert await old_prefix_objects_remain(inner)
    moved = await relocate_blobs(inner)
    assert moved == 4
    assert set(inner.objects) == {
        f"{tenant_prefix(1)}fixtures/sha256/aa",
        f"{tenant_prefix(1)}trajectories/1/r.json",
        f"{tenant_prefix(1)}artifacts/1/r/out.md",
        f"{tenant_prefix(1)}workspaces/1/r.json",
        "tenants/1/trajectories/1/already.json",
    }
    assert not await old_prefix_objects_remain(inner)
    assert await relocate_blobs(inner) == 0


async def test_has_any_on_the_fake_store():
    inner = DictBlobStore()
    assert not await inner.has_any("p/")
    await inner.put_object("p/x", b"1")
    assert await inner.has_any("p/")
