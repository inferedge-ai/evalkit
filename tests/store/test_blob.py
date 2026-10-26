import pytest

from evalkit.core.blob import artifact_key, fixture_blob_key, trajectory_key
from evalkit.store.blob import Obstore


@pytest.fixture
def blob_store(tmp_path):
    return Obstore(f"file://{tmp_path}")


async def test_round_trip(blob_store):
    await blob_store.put_object("a/b.txt", b"hello")
    assert await blob_store.get_object("a/b.txt") == b"hello"


async def test_list_under_prefix(blob_store):
    await blob_store.put_object("p/one.txt", b"1")
    await blob_store.put_object("p/two.txt", b"22")
    await blob_store.put_object("q/three.txt", b"3")
    assert sorted(await blob_store.list_objects("p/")) == [("p/one.txt", 1), ("p/two.txt", 2)]


async def test_exists_and_delete(blob_store):
    await blob_store.put_object("x.txt", b"x")
    assert await blob_store.object_exists("x.txt")
    await blob_store.delete_object("x.txt")
    assert not await blob_store.object_exists("x.txt")


def test_key_helpers():
    assert trajectory_key(7, "run1") == "trajectories/7/run1.json"
    assert artifact_key(7, "run1", "/outputs/r.xlsx") == "artifacts/7/run1/outputs/r.xlsx"
    assert fixture_blob_key("ab12") == "fixtures/sha256/ab12"


async def test_object_url_joins_store_url_and_key():
    assert await Obstore("s3://bucket").object_url("fixtures/a.csv") == (
        "s3://bucket/fixtures/a.csv"
    )
    assert await Obstore("s3://bucket/").object_url("fixtures/a.csv") == (
        "s3://bucket/fixtures/a.csv"
    )
