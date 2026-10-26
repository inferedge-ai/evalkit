import pytest
from sqlalchemy.exc import IntegrityError

from evalkit.core.blob import fixture_blob_key
from evalkit.db.cases import current_cases, files_for, get_dataset
from evalkit.db.fixture_blobs import known_blobs, record_blob, workspace_fixtures
from evalkit.db.models import FixtureBlob
from evalkit.store.base import CaseFile
from tests.seed import World, count, incoming, seed_world, sync

SHA_A = "a" * 64
SHA_B = "b" * 64


@pytest.fixture
async def world(session_factory) -> World:
    world = await seed_world(session_factory)
    async with session_factory() as s:
        await record_blob(s, SHA_A, 10, world.akshat_id)
        await record_blob(s, SHA_B, 20, None)
        await s.commit()
    return world


def file(key: str, sha256: str) -> CaseFile:
    return CaseFile(key=key, sha256=sha256, context=f"{key} context")


async def test_record_blob_twice_keeps_one_row_and_first_uploader(session_factory, world):
    async with session_factory() as s:
        await record_blob(s, SHA_A, 10, world.admin_id)
        await s.commit()
        row = await s.get(FixtureBlob, SHA_A)
    assert await count(session_factory, FixtureBlob) == 2
    assert row is not None and row.uploaded_by == world.akshat_id


async def test_known_blobs(session_factory, world):
    async with session_factory() as s:
        assert await known_blobs(s, [SHA_A, "c" * 64]) == {SHA_A}
        assert await known_blobs(s, []) == set()


def test_blob_key_is_content_addressed():
    assert fixture_blob_key(SHA_A) == f"fixtures/sha256/{SHA_A}"


async def test_files_keep_yaml_order(session_factory, world):
    case = incoming("q-1", files=(file("z.pdf", SHA_B), file("a.pdf", SHA_A)))
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [case])
        await s.commit()
        [view] = await current_cases(s, await get_dataset(s, "dev", "finance"))
        found = await files_for(s, [(view.case_ref, view.content_hash)])
    assert [f.key for f in view.files] == ["z.pdf", "a.pdf"]
    assert found[(view.case_ref, view.content_hash)] == view.files


async def test_file_without_blob_fails(session_factory, world):
    case = incoming("q-1", files=(file("x.pdf", "c" * 64),))
    async with session_factory() as s:
        with pytest.raises(IntegrityError, match="fixture_blobs"):
            await sync(s, world.dev, world.admin_id, [case])
            await s.flush()


async def test_workspace_fixtures_lists_only_current_cases_in_that_workspace(
    session_factory, world
):
    a = incoming("q-a", files=(file("report.pdf", SHA_A),))
    b = incoming("q-b", files=(file("other.pdf", SHA_B),))
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [a, b], dataset="finance")
        await sync(s, world.dev, world.admin_id, [a], dataset="finance-lite")
        await sync(s, world.dev, world.admin_id, [a], dataset="finance")
        await sync(s, world.prod, world.admin_id, [b], dataset="finance")
        await s.commit()
        dev = await workspace_fixtures(s, world.dev.id)
        prod = await workspace_fixtures(s, world.prod.id)
        searched = await workspace_fixtures(s, world.dev.id, text="REPORT")
        empty = await workspace_fixtures(s, world.dev.id, text="nope")
    assert [(f.key, f.datasets, f.size, f.uploaded_by) for f in dev.items] == [
        ("report.pdf", ["finance", "finance-lite"], 10, "akshat")
    ]
    assert dev.total == 1
    assert [(f.key, f.uploaded_by) for f in prod.items] == [("other.pdf", None)]
    assert [f.key for f in searched.items] == ["report.pdf"]
    assert empty.total == 0 and empty.items == []
