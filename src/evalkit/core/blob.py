from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from evalkit.core.paths import require_safe_path, require_safe_run_id
from evalkit.store.base import BlobStream


class BlobStore(Protocol):
    async def put_object(self, key: str, data: bytes) -> None: ...

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None: ...

    async def get_object(self, key: str) -> bytes: ...

    async def get_stream(self, key: str) -> BlobStream: ...

    async def object_exists(self, key: str) -> bool: ...

    async def list_objects(self, prefix: str) -> list[tuple[str, int]]: ...

    async def delete_object(self, key: str) -> None: ...

    async def copy_object(self, source: str, destination: str) -> None: ...

    async def object_url(self, key: str) -> str: ...

    async def has_any(self, prefix: str) -> bool: ...


def trajectory_key(agent_id: int, harness_run_id: str) -> str:
    return f"trajectories/{agent_id}/{harness_run_id}.json"


def artifact_key(agent_id: int, harness_run_id: str, path: str) -> str:
    require_safe_run_id(harness_run_id)
    if path:
        path = require_safe_path(path)
    return f"artifacts/{agent_id}/{harness_run_id}/{path}"


def workspace_key(agent_id: int, harness_run_id: str) -> str:
    return f"workspaces/{agent_id}/{harness_run_id}.json"


def fixture_blob_key(sha256: str) -> str:
    return f"fixtures/sha256/{sha256}"


TENANT_PREFIX = "tenants/"


def tenant_prefix(tenant_id: int) -> str:
    return f"{TENANT_PREFIX}{tenant_id}/"


class TenantBlobStore:
    def __init__(self, inner: BlobStore, tenant_id: int):
        self._inner = inner
        self._prefix = tenant_prefix(tenant_id)

    def _key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    async def put_object(self, key: str, data: bytes) -> None:
        await self._inner.put_object(self._key(key), data)

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        await self._inner.put_stream(self._key(key), chunks)

    async def get_object(self, key: str) -> bytes:
        return await self._inner.get_object(self._key(key))

    async def get_stream(self, key: str) -> BlobStream:
        return await self._inner.get_stream(self._key(key))

    async def object_exists(self, key: str) -> bool:
        return await self._inner.object_exists(self._key(key))

    async def list_objects(self, prefix: str) -> list[tuple[str, int]]:
        found = await self._inner.list_objects(self._key(prefix))
        return [(key.removeprefix(self._prefix), size) for key, size in found]

    async def delete_object(self, key: str) -> None:
        await self._inner.delete_object(self._key(key))

    async def copy_object(self, source: str, destination: str) -> None:
        await self._inner.copy_object(self._key(source), self._key(destination))

    async def object_url(self, key: str) -> str:
        return await self._inner.object_url(self._key(key))

    async def has_any(self, prefix: str) -> bool:
        return await self._inner.has_any(self._key(prefix))
