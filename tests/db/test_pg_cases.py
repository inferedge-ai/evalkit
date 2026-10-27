import pytest
from sqlalchemy import select

from evalkit.core.review import CaseEventKind
from evalkit.db.cases import current_cases, get_dataset, splits
from evalkit.db.models import Case, CaseEvent, CaseVersion, DatasetSync
from evalkit.store.base import UnknownDataset
from tests.seed import World, count, incoming, overlay, seed_world, sync


@pytest.fixture
async def world(session_factory) -> World:
    return await seed_world(session_factory)


async def test_same_case_two_datasets_different_versions(session_factory, world):
    v1 = incoming("q-123", question="What is EBITDA?")
    v2 = incoming("q-123", question="What is EBITDA for FY24?")
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [v1])
        await sync(s, world.akshat_ws, world.akshat_id, [v2])
        await s.commit()
        dev = await current_cases(s, await get_dataset(s, "dev", "finance"))
        mine = await current_cases(s, await get_dataset(s, "akshat", "finance"))
    assert dev[0].content_hash == v1.content_hash
    assert mine[0].content_hash == v2.content_hash
    assert await count(session_factory, Case) == 1
    assert await count(session_factory, CaseVersion) == 2


async def test_path_tags_and_source_belong_to_each_dataset(session_factory, world):
    case = incoming("q-1", tags=("ebitda",))
    in_dev = case.model_copy(update={"path_tags": ["statements"], "source": "statements/a.yaml"})
    mine = case.model_copy(update={"path_tags": ["mine"], "source": "a.yaml"})
    moved = case.model_copy(update={"path_tags": ["ratios"], "source": "ratios/a.yaml"})
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [in_dev])
        await sync(s, world.akshat_ws, world.akshat_id, [mine])
        dev_ds = await get_dataset(s, "dev", "finance")
        [before] = await current_cases(s, dev_ds)
        again = await sync(s, world.dev, world.admin_id, [moved])
        [after] = await current_cases(s, dev_ds)
        [personal] = await current_cases(s, await get_dataset(s, "akshat", "finance"))
        await s.commit()
    assert before.split_tags == ["ebitda", "statements"]
    assert before.source == "statements/a.yaml"
    assert again.unchanged == 1 and again.patched == []
    assert after.split_tags == ["ebitda", "ratios"] and after.source == "ratios/a.yaml"
    assert personal.split_tags == ["ebitda", "mine"] and personal.source == "a.yaml"
    assert after.content_hash == personal.content_hash


async def test_resync_same_content_is_unchanged_and_shares_version(session_factory, world):
    case = incoming("q-1")
    async with session_factory() as s:
        first = await sync(s, world.dev, world.admin_id, [case])
        again = await sync(s, world.dev, world.admin_id, [case])
        other = await sync(s, world.prod, world.admin_id, [case])
        await s.commit()
    assert first.created == ["q-1"]
    assert again.created == [] and again.patched == [] and again.unchanged == 1
    assert other.created == ["q-1"]
    assert await count(session_factory, CaseVersion) == 1


async def test_changed_content_is_patched(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        result = await sync(s, world.dev, world.admin_id, [incoming("q-1", question="new")])
        await s.commit()
    assert result.patched == ["q-1"]
    assert await count(session_factory, CaseVersion) == 2


async def test_full_sync_soft_removes_and_resync_restores(session_factory, world):
    a, b = incoming("q-a"), incoming("q-b")
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [a, b])
        removed = await sync(s, world.dev, world.admin_id, [a])
        dataset = await get_dataset(s, "dev", "finance")
        after_remove = [v.case_id for v in await current_cases(s, dataset)]
        restored = await sync(s, world.dev, world.admin_id, [a, b])
        after_restore = [v.case_id for v in await current_cases(s, dataset)]
        await s.commit()
    assert removed.removed == ["q-b"]
    assert after_remove == ["q-a"]
    assert restored.patched == ["q-b"]
    assert after_restore == ["q-a", "q-b"]


async def test_partial_sync_never_removes(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-a"), incoming("q-b")])
        result = await sync(s, world.dev, world.admin_id, [incoming("q-a")], full=False)
        cases = await current_cases(s, await get_dataset(s, "dev", "finance"))
        await s.commit()
    assert result.removed == []
    assert [v.case_id for v in cases] == ["q-a", "q-b"]


async def test_overlay_applies_in_every_dataset_of_workspace_holding_target(session_factory, world):
    target = incoming("q-1")
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [target], dataset="finance")
        await sync(s, world.dev, world.admin_id, [target], dataset="finance-lite")
        await sync(s, world.prod, world.admin_id, [target], dataset="finance")
        result = await sync(
            s,
            world.dev,
            world.admin_id,
            [],
            dataset="client-cases",
            overlays=(overlay("q-1", ["support-bot"]),),
        )
        views = {
            (ws, name): await current_cases(s, await get_dataset(s, ws, name), agent="support-bot")
            for ws, name in [("dev", "finance"), ("dev", "finance-lite"), ("prod", "finance")]
        }
        other_agent = await current_cases(s, await get_dataset(s, "dev", "finance"), agent="fin")
        await s.commit()
    assert result.overlays == 1
    tone = {"tone": {"strict": True}}
    assert views[("dev", "finance")][0].effective_evaluators["custom"] == tone
    assert views[("dev", "finance-lite")][0].effective_evaluators["custom"] == tone
    assert "custom" not in views[("prod", "finance")][0].effective_evaluators
    assert "custom" not in other_agent[0].effective_evaluators
    assert views[("dev", "finance")][0].evaluators == other_agent[0].evaluators


async def test_overlay_with_unknown_target_is_skipped(session_factory, world):
    async with session_factory() as s:
        result = await sync(
            s,
            world.dev,
            world.admin_id,
            [],
            dataset="client-cases",
            overlays=(overlay("nope", ["m"]),),
        )
        await s.commit()
    assert result.overlays == 0


async def test_split_counts_include_overlay_tags(session_factory, world):
    async with session_factory() as s:
        await sync(
            s,
            world.dev,
            world.admin_id,
            [incoming("q-1", tags=("core",)), incoming("q-2", tags=("core", "hard"))],
        )
        await sync(
            s,
            world.dev,
            world.admin_id,
            [],
            dataset="client-cases",
            overlays=(overlay("q-1", ["support-bot"], tags=("client-only",)),),
        )
        counts = await splits(s, await get_dataset(s, "dev", "finance"))
        await s.commit()
    assert counts == [("client-only", 1), ("core", 2), ("hard", 1)]


async def test_dataset_syncs_row_records_git_detail_and_counts(session_factory, world):
    detail = {"sha": "abc123", "branch": "develop"}
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1"), incoming("q-2")], detail=detail)
        result = await sync(
            s, world.dev, world.admin_id, [incoming("q-1", question="new")], detail=detail
        )
        await s.commit()
        row = await s.get(DatasetSync, result.sync_id)
        dataset = await get_dataset(s, "dev", "finance")
    assert row is not None
    assert row.detail == {
        **detail,
        "counts": {"created": 0, "patched": 1, "removed": 1, "unchanged": 0},
    }
    assert dataset.last_sync is not None
    assert dataset.last_sync.id == result.sync_id
    assert dataset.last_sync.actor == "admin"


async def test_every_change_writes_a_synced_event(session_factory, world):
    async with session_factory() as s:
        result = await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        await s.commit()
        events = (await s.scalars(select(CaseEvent))).all()
    assert [(e.kind, e.sync_id) for e in events] == [(CaseEventKind.SYNCED, result.sync_id)]


async def test_review_state_only_on_shared_suites(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        await sync(s, world.akshat_ws, world.akshat_id, [incoming("q-1")])
        await s.commit()
        shared = await current_cases(s, await get_dataset(s, "dev", "finance"))
        personal = await current_cases(s, await get_dataset(s, "akshat", "finance"))
    assert shared[0].review_state == "unreviewed"
    assert personal[0].review_state is None


async def test_unknown_dataset(session_factory, world):
    async with session_factory() as s:
        with pytest.raises(UnknownDataset):
            await get_dataset(s, "dev", "nope")


async def _decide(s, world, kind: CaseEventKind, target_id: int | None = None) -> None:
    dataset = await get_dataset(s, "dev", "finance")
    [view] = await current_cases(s, dataset)
    s.add(
        CaseEvent(
            dataset_id=dataset.id,
            case_ref=view.case_ref,
            content_hash=view.content_hash,
            kind=kind.value,
            actor_id=world.admin_id,
            target_id=target_id,
        )
    )
    await s.flush()


async def test_new_content_resets_approved_to_draft(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        await _decide(s, world, CaseEventKind.APPROVED)
        approved = await current_cases(s, await get_dataset(s, "dev", "finance"))
        result = await sync(s, world.dev, world.admin_id, [incoming("q-1", question="new")])
        after = await current_cases(s, await get_dataset(s, "dev", "finance"))
        await s.commit()
    assert approved[0].review_state == "approved"
    assert result.now_unreviewed == ["q-1"]
    assert result.carried == []
    assert after[0].review_state == "unreviewed"


async def test_requested_review_carries_to_new_content(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        await _decide(s, world, CaseEventKind.REVIEW_REQUESTED, target_id=world.akshat_id)
        result = await sync(s, world.dev, world.admin_id, [incoming("q-1", question="new")])
        after = await current_cases(s, await get_dataset(s, "dev", "finance"))
        await s.commit()
    assert [(c.case_id, c.reviewer) for c in result.carried] == [("q-1", "akshat")]
    assert result.now_unreviewed == []
    assert after[0].review_state == "review_requested"


async def test_review_state_is_per_dataset(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        await sync(s, world.prod, world.admin_id, [incoming("q-1")])
        await _decide(s, world, CaseEventKind.APPROVED)
        dev = await current_cases(s, await get_dataset(s, "dev", "finance"))
        prod = await current_cases(s, await get_dataset(s, "prod", "finance"))
        await s.commit()
    assert dev[0].review_state == "approved"
    assert prod[0].review_state == "unreviewed"
