from evalkit.store.base import BlobStream


async def _once(data: bytes):
    yield data


class DictBlobStore:
    def __init__(self, url: str = "memory://blob"):
        self.url = url
        self.objects: dict[str, bytes] = {}

    async def put_object(self, key: str, data: bytes) -> None:
        self.objects[key] = data

    async def put_stream(self, key, chunks) -> None:
        self.objects[key] = b"".join([chunk async for chunk in chunks])

    async def get_object(self, key: str) -> bytes:
        return self.objects[key]

    async def get_stream(self, key: str) -> BlobStream:
        data = self.objects[key]
        return BlobStream(len(data), _once(data))

    async def object_exists(self, key: str) -> bool:
        return key in self.objects

    async def list_objects(self, prefix: str) -> list[tuple[str, int]]:
        return sorted((k, len(v)) for k, v in self.objects.items() if k.startswith(prefix))

    async def delete_object(self, key: str) -> None:
        self.objects.pop(key, None)

    async def copy_object(self, source: str, destination: str) -> None:
        self.objects[destination] = self.objects[source]

    async def object_url(self, key: str) -> str:
        return f"{self.url.rstrip('/')}/{key}"
