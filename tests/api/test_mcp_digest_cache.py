import fakeredis

from evalkit.api import runs as runs_api
from evalkit.api.mcp.digest_cache import (
    CachedDigest,
    MemoryDigestCache,
    RedisDigestCache,
    digest_key,
)
from evalkit.contracts.atif import Trajectory
from evalkit.core.run_digest import digest, turns
from tests.api.native_api import api_env
from tests.api.test_mcp import call, fake_with_runs
from tests.mcp_trajectory import chat_session


def fake_with_sessions(trajectory: dict | None = None):
    fake = fake_with_runs()
    if trajectory is not None:
        fake.trajectory_doc = trajectory
    for run_id, run in fake.runs.items():
        run["session_id"] = f"sess-{run_id}"
    return fake


def unanswered_session() -> dict:
    doc = chat_session()
    last = doc["steps"][-1]
    doc["steps"].append(
        {
            "step_id": last["step_id"] + 1,
            "timestamp": last["timestamp"],
            "source": "user",
            "message": "And the returns policy?",
        }
    )
    return doc


def count_reads(monkeypatch) -> list[str]:
    reads: list[str] = []
    read = runs_api.read_trajectory

    async def counting(record, run_id, *args):
        reads.append(run_id)
        return await read(record, run_id, *args)

    monkeypatch.setattr(runs_api, "read_trajectory", counting)
    return reads


def cached_digest() -> CachedDigest:
    trajectory = Trajectory.model_validate(chat_session())
    return CachedDigest(digest=digest("s-1", trajectory, set()), turns=turns(trajectory))


async def test_an_answered_session_head_is_read_from_the_harness_once(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    async with api_env(tmp_path, session_factory, fake_with_sessions()) as (client, _):
        reads = count_reads(monkeypatch)
        first = await call(client, "get_run_stats", agent="fake@canonical", group_by="tool")
        again = await call(client, "get_run_stats", agent="fake@canonical", group_by="tool")
        listed = await call(client, "find_runs", agent="fake@canonical", include=["metrics"])
    assert sorted(reads) == ["s-1", "s-2", "s-3"]
    assert again["data"] == first["data"] and again["meta"]["unavailable"] == 0
    assert all(row["metrics"]["turns"] for row in listed["data"])


async def test_runs_outside_a_session_are_read_on_every_call(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    async with api_env(tmp_path, session_factory, fake_with_runs()) as (client, _):
        reads = count_reads(monkeypatch)
        await call(client, "get_run_stats", agent="fake@canonical")
        await call(client, "get_run_stats", agent="fake@canonical")
    assert len(reads) == 6


async def test_a_session_still_waiting_for_an_answer_is_read_again(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    fake = fake_with_sessions(unanswered_session())
    async with api_env(tmp_path, session_factory, fake) as (client, _):
        reads = count_reads(monkeypatch)
        await call(client, "get_run_stats", agent="fake@canonical")
        await call(client, "get_run_stats", agent="fake@canonical")
    assert len(reads) == 6


async def test_get_runs_always_reads_the_harness(
    tmp_path, stub_judges, session_factory, monkeypatch
):
    async with api_env(tmp_path, session_factory, fake_with_sessions()) as (client, _):
        await call(client, "get_run_stats", agent="fake@canonical")
        reads = count_reads(monkeypatch)
        await call(client, "get_runs", agent="fake@canonical", run_ids=["s-1"])
        await call(client, "get_runs", agent="fake@canonical", run_ids=["s-1"])
    assert reads == ["s-1", "s-1"]


async def test_the_memory_cache_drops_the_least_recently_used_entry():
    cache = MemoryDigestCache(max_entries=2)
    value = cached_digest()
    await cache.put("a", value)
    await cache.put("b", value)
    await cache.get_many(["a"])
    await cache.put("c", value)
    assert set(await cache.get_many(["a", "b", "c"])) == {"a", "c"}


async def test_the_redis_cache_round_trips_a_digest_with_its_ttl():
    cache = RedisDigestCache("redis://cache.test:6379/0", ttl_seconds=60)
    cache.client = fakeredis.FakeAsyncRedis()
    key = digest_key(7, "s-1")
    value = cached_digest()
    await cache.put(key, value)
    assert await cache.get_many([key, digest_key(7, "s-2")]) == {key: value}
    assert 0 < await cache.client.ttl(key) <= 60


async def test_an_unreachable_redis_reads_as_a_miss_and_drops_writes():
    cache = RedisDigestCache("redis://127.0.0.1:1/0", ttl_seconds=60)
    await cache.put("k", cached_digest())
    assert await cache.get_many(["k"]) == {}


async def test_an_entry_that_no_longer_parses_reads_as_a_miss():
    cache = RedisDigestCache("redis://cache.test:6379/0", ttl_seconds=60)
    cache.client = fakeredis.FakeAsyncRedis()
    good, bad = digest_key(7, "s-1"), digest_key(7, "s-2")
    value = cached_digest()
    await cache.put(good, value)
    await cache.client.set(bad, '{"digest": {}}')
    assert await cache.get_many([good, bad]) == {good: value}
