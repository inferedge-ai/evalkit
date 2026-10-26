from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from tempfile import mkdtemp

from evalkit.client.api import AsyncApi
from evalkit.store.base import BlobStream, CaseFile

FIXTURE_PREFIX = "fixtures/sha256/"
CHECK_PREFIX = "checks/"


class HttpBlobStore:
    def __init__(self, api: AsyncApi, experiment_id: int | None, cache_dir: Path | None = None):
        self._api = api
        self._experiment_id = experiment_id
        self._configured_dir = cache_dir
        self._scratch_dir: Path | None = None

    def _cache_dir(self) -> Path:
        if self._configured_dir is not None:
            return self._configured_dir
        if self._scratch_dir is None:
            self._scratch_dir = Path(mkdtemp(prefix="evalkit-fixtures-"))
        return self._scratch_dir

    def _local_path(self, key: str) -> Path | None:
        if key.startswith(FIXTURE_PREFIX):
            return self._cache_dir() / ".sha256" / key.removeprefix(FIXTURE_PREFIX)
        if key.startswith(CHECK_PREFIX):
            return self._cache_dir() / ".checks" / key.removeprefix(CHECK_PREFIX)
        return None

    def _experiment_path(self) -> str:
        if self._experiment_id is None:
            raise ValueError("no experiment bound; only fixture and check keys are available")
        return f"/api/experiments/{self._experiment_id}/blob"

    def _remote(self, key: str) -> str:
        return f"{self._experiment_path()}/{key}"

    async def put_object(self, key: str, data: bytes) -> None:
        local = self._local_path(key)
        if local is not None:
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(data)
            return
        await self._api.put(self._remote(key), content=data, retry=True)

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        await self.put_object(key, b"".join([chunk async for chunk in chunks]))

    async def _cached(self, key: str, local: Path) -> Path:
        if not local.is_file() and key.startswith(FIXTURE_PREFIX):
            resp = await self._api.get(f"/api/fixtures/sha256/{key.removeprefix(FIXTURE_PREFIX)}")
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(resp.content)
        return local

    async def get_object(self, key: str) -> bytes:
        local = self._local_path(key)
        if local is None:
            return (await self._api.get(self._remote(key))).content
        return (await self._cached(key, local)).read_bytes()

    async def get_stream(self, key: str) -> BlobStream:
        data = await self.get_object(key)
        return BlobStream(len(data), _once(data))

    async def fixture_path(self, file: CaseFile) -> Path:
        named = self._cache_dir() / file.sha256 / file.key
        if not named.is_file():
            named.parent.mkdir(parents=True, exist_ok=True)
            named.write_bytes(await self.get_object(f"{FIXTURE_PREFIX}{file.sha256}"))
        return named

    async def object_exists(self, key: str) -> bool:
        local = self._local_path(key)
        if local is not None:
            return local.is_file()
        return (await self._api.head(self._remote(key))).status_code == 200

    async def list_objects(self, prefix: str) -> list[tuple[str, int]]:
        if self._local_path(prefix) is not None:
            raise ValueError("listing local keys is not supported")
        body = (await self._api.get(self._experiment_path(), params={"prefix": prefix})).json()
        return [(str(key), int(size)) for key, size in body["objects"]]

    async def delete_object(self, key: str) -> None:
        raise NotImplementedError

    async def copy_object(self, source: str, destination: str) -> None:
        raise NotImplementedError

    async def object_url(self, key: str) -> str:
        local = self._local_path(key)
        if local is None:
            raise ValueError(f"{key} has no URL a harness can fetch")
        return (await self._cached(key, local)).resolve().as_uri()


async def _once(data: bytes) -> AsyncIterator[bytes]:
    yield data
