from __future__ import annotations

from collections.abc import AsyncIterator

import obstore
from obstore.exceptions import NotFoundError
from obstore.store import ObjectStore, from_url

from evalkit.store.base import BlobStream

CHUNK_BYTES = 1024 * 1024

_stores: dict[str, ObjectStore] = {}


def _store_for(url: str) -> ObjectStore:
    if url not in _stores:
        _stores[url] = from_url(url, mkdir=True) if url.startswith("file://") else from_url(url)
    return _stores[url]


class Obstore:
    def __init__(self, url: str):
        self.url = url

    @property
    def _store(self) -> ObjectStore:
        return _store_for(self.url)

    async def put_object(self, key: str, data: bytes) -> None:
        await obstore.put_async(self._store, key, data)

    async def put_stream(self, key: str, chunks: AsyncIterator[bytes]) -> None:
        await obstore.put_async(self._store, key, chunks)

    async def get_object(self, key: str) -> bytes:
        resp = await obstore.get_async(self._store, key)
        return bytes(await resp.bytes_async())

    async def get_stream(self, key: str) -> BlobStream:
        resp = await obstore.get_async(self._store, key)
        return BlobStream(resp.meta["size"], _chunks(resp.stream(min_chunk_size=CHUNK_BYTES)))

    async def object_exists(self, key: str) -> bool:
        try:
            await obstore.head_async(self._store, key)
            return True
        except (NotFoundError, FileNotFoundError):
            return False

    async def list_objects(self, prefix: str) -> list[tuple[str, int]]:
        objects: list[tuple[str, int]] = []
        async for chunk in obstore.list(self._store, prefix=prefix):
            objects.extend((obj["path"], obj["size"]) for obj in chunk)
        return objects

    async def delete_object(self, key: str) -> None:
        await obstore.delete_async(self._store, key)

    async def copy_object(self, source: str, destination: str) -> None:
        await obstore.copy_async(self._store, source, destination)

    async def object_url(self, key: str) -> str:
        return f"{self.url.rstrip('/')}/{key}"


async def _chunks(stream: AsyncIterator) -> AsyncIterator[bytes]:
    async for chunk in stream:
        yield bytes(chunk)
