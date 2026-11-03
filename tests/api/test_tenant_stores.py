from evalkit.api.access import build_tenant_stores
from evalkit.core.blob import tenant_prefix
from tests.api.native_api import api_env


async def test_tenant_stores_are_built_per_tenant(tmp_path, session_factory):
    async with api_env(tmp_path, session_factory) as (client, env):
        stores = build_tenant_stores(client.app.state, 1)
        assert stores.tenant_id == 1
        assert stores.store is not env.store
        assert (await stores.workspaces.get_by_name("canonical")).tenant_id == 1
        await stores.blob.put_object("probe", b"x")
        assert f"{tenant_prefix(1)}probe" in env.blob.objects
        assert stores.deps.store is stores.store
        assert stores.deps.blob_store is stores.blob
        resp = await client.get("/api/auth/me")
        assert resp.status_code == 200
