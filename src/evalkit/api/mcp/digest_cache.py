import logging
from collections import OrderedDict
from typing import Protocol

import redis.asyncio as redis
from pydantic import BaseModel, ValidationError
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from evalkit.config import CacheConfig
from evalkit.core.run_digest import RunDigest, Turn

LOGGER = logging.getLogger(__name__)
# Bump when RunDigest or Turn change shape, so entries written by an older
# server are never read as the new shape.
DIGEST_VERSION = 1
REDIS_TIMEOUT_SECONDS = 1
TTL_SECONDS = 30 * 24 * 3600
MEMORY_ENTRIES = 5000


class CachedDigest(BaseModel):
    digest: RunDigest
    turns: list[Turn]


class DigestCache(Protocol):
    async def get_many(self, keys: list[str]) -> dict[str, CachedDigest]: ...

    async def put(self, key: str, value: CachedDigest) -> None: ...

    async def aclose(self) -> None: ...


def digest_key(agent_id: int, run_id: str) -> str:
    return f"evalkit:digest:v{DIGEST_VERSION}:{agent_id}:{run_id}"


class MemoryDigestCache:
    def __init__(self, max_entries: int) -> None:
        self.max_entries = max_entries
        self.entries: OrderedDict[str, CachedDigest] = OrderedDict()

    async def get_many(self, keys: list[str]) -> dict[str, CachedDigest]:
        found = {key: self.entries[key] for key in keys if key in self.entries}
        for key in found:
            self.entries.move_to_end(key)
        return found

    async def put(self, key: str, value: CachedDigest) -> None:
        self.entries[key] = value
        self.entries.move_to_end(key)
        while len(self.entries) > self.max_entries:
            self.entries.popitem(last=False)

    async def aclose(self) -> None:
        self.entries.clear()


class RedisDigestCache:
    def __init__(self, url: str, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self.client = redis.Redis.from_url(
            url,
            socket_timeout=REDIS_TIMEOUT_SECONDS,
            socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
            retry=Retry(NoBackoff(), 0),
        )

    # The cache only saves harness reads: when Redis is unreachable, or an entry
    # no longer parses, a call reads the harness as it would without one.
    async def get_many(self, keys: list[str]) -> dict[str, CachedDigest]:
        if not keys:
            return {}
        try:
            values = await self.client.mget(keys)
        except redis.RedisError as exc:
            LOGGER.warning("mcp.digest_cache_read_failed: %s", exc)
            return {}
        found: dict[str, CachedDigest] = {}
        for key, value in zip(keys, values, strict=True):
            if value is None:
                continue
            try:
                found[key] = CachedDigest.model_validate_json(value)
            except ValidationError as exc:
                LOGGER.warning("mcp.digest_cache_entry_invalid: %s: %s", key, exc)
        return found

    async def put(self, key: str, value: CachedDigest) -> None:
        try:
            await self.client.set(key, value.model_dump_json(), ex=self.ttl_seconds)
        except redis.RedisError as exc:
            LOGGER.warning("mcp.digest_cache_write_failed: %s", exc)

    async def aclose(self) -> None:
        await self.client.aclose()


def digest_cache(cfg: CacheConfig) -> DigestCache:
    if cfg.redis_url:
        return RedisDigestCache(cfg.redis_url, TTL_SECONDS)
    return MemoryDigestCache(MEMORY_ENTRIES)
