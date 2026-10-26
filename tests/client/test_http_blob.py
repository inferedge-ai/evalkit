import hashlib

import httpx
import pytest
import respx

from evalkit.client.api import ApiRequestError, AsyncApi
from evalkit.client.blob import HttpBlobStore
from evalkit.client.store import HttpStore, local_holder
from evalkit.core.blob import fixture_blob_key
from evalkit.core.runner import ExperimentRequest
from evalkit.store.base import CaseFile
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent
from tests.native import put_fixture, seed_env, serve


@pytest.fixture
async def ctx(tmp_path, stub_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    await seed_agent(session_factory, "fake", "admin")
    async with serve(env, tmp_path) as client:
        token = client.headers["Authorization"].removeprefix("Bearer ")
        api = AsyncApi("http://test", token, transport=client._transport)
        yield api, env
        await api.aclose()


async def test_experiment_keys_go_over_http(ctx, tmp_path):
    api, _ = ctx
    exp, _ = await HttpStore(api).start_experiment(
        ExperimentRequest(
            dataset="finance@canonical",
            agent="fake@admin",
            capabilities=FakeHarness().capabilities,
            holder=local_holder("admin"),
        )
    )
    blob_store = HttpBlobStore(api, exp.id, tmp_path / "cache")
    key = f"artifacts/{exp.agent_id}/r1/out.txt"
    await blob_store.put_object(key, b"hi")
    assert await blob_store.object_exists(key)
    assert not await blob_store.object_exists(f"artifacts/{exp.agent_id}/r1/no")
    assert await blob_store.get_object(key) == b"hi"
    assert await blob_store.list_objects(f"artifacts/{exp.agent_id}/r1/") == [(key, 2)]
    with pytest.raises(ValueError):
        await blob_store.object_url(key)
    with pytest.raises(ApiRequestError, match="400"):
        await blob_store.put_object(f"artifacts/{exp.agent_id + 999}/r1/x", b"no")


async def test_fixture_keys_are_cached_locally_with_server_fallback(ctx, tmp_path):
    api, env = ctx
    cache = tmp_path / "cache"
    file = await put_fixture(env, b"remote", "remote.csv")
    blob_store = HttpBlobStore(api, None, cache)
    key = fixture_blob_key(file.sha256)
    assert await blob_store.get_object(key) == b"remote"
    assert (cache / ".sha256" / file.sha256).read_bytes() == b"remote"
    assert await blob_store.object_url(key) == (cache / ".sha256" / file.sha256).as_uri()
    named = await blob_store.fixture_path(file)
    assert named == cache / file.sha256 / "remote.csv" and named.read_bytes() == b"remote"
    await blob_store.put_object("checks/evalkit-check.txt", b"check")
    assert (cache / ".checks" / "evalkit-check.txt").read_bytes() == b"check"
    assert (
        await blob_store.object_url("checks/evalkit-check.txt")
        == (cache / ".checks" / "evalkit-check.txt").as_uri()
    )
    with pytest.raises(ValueError):
        await blob_store.put_object("trajectories/x/y.json", b"{}")


@respx.mock
async def test_fixture_downloads_once(tmp_path):
    digest = hashlib.sha256(b"x,y").hexdigest()
    route = respx.get(f"http://srv/api/fixtures/sha256/{digest}").mock(
        return_value=httpx.Response(200, content=b"x,y")
    )
    api = AsyncApi("http://srv", "tok")
    blob = HttpBlobStore(api, None, tmp_path)
    file = CaseFile(key="a.csv", sha256=digest, context="c")
    path = await blob.fixture_path(file)
    assert path.name == "a.csv" and path.read_bytes() == b"x,y"
    assert (await blob.fixture_path(file)) == path
    assert await blob.get_object(fixture_blob_key(digest)) == b"x,y"
    assert route.call_count == 1
    await api.aclose()


async def test_fixture_keys_go_to_a_scratch_dir_without_a_cache_directory(ctx):
    api, env = ctx
    file = await put_fixture(env, b"remote", "remote.csv")
    blob_store = HttpBlobStore(api, None)
    url = await blob_store.object_url(fixture_blob_key(file.sha256))
    assert url.startswith("file://") and "evalkit-fixtures-" in url
    assert await blob_store.get_object(fixture_blob_key(file.sha256)) == b"remote"
