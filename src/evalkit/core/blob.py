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
