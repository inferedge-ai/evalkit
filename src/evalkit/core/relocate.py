# TODO: remove in 0.6.0, two minor versions after 0.4.0 moved blobs under tenants/
from evalkit.core.blob import BlobStore, tenant_prefix

OLD_PREFIXES = ("fixtures/", "trajectories/", "artifacts/", "workspaces/")


async def old_prefix_objects_remain(blob: BlobStore) -> bool:
    for prefix in OLD_PREFIXES:
        if await blob.has_any(prefix):
            return True
    return False


async def relocate_blobs(blob: BlobStore, tenant_id: int = 1) -> int:
    moved = 0
    for prefix in OLD_PREFIXES:
        for key, _ in await blob.list_objects(prefix):
            new_key = f"{tenant_prefix(tenant_id)}{key}"
            if not await blob.object_exists(new_key):
                await blob.copy_object(key, new_key)
            assert await blob.object_exists(new_key)
            await blob.delete_object(key)
            moved += 1
    return moved
