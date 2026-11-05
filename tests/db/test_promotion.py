import pytest
from sqlalchemy import select

from evalkit.core.promotion import MissingTargetDataset
from evalkit.db.cases import create_dataset, current_cases, get_dataset
from evalkit.db.models import CaseEvent, DatasetSync, Overlay
from evalkit.db.promotion import execute_promotion, plan_promotion, twin_status
from evalkit.db.workspaces import WorkspaceStore
from tests.seed import World, incoming, overlay, seed_world, sync


@pytest.fixture
async def world(session_factory) -> World:
    world = await seed_world(session_factory)
    await WorkspaceStore(session_factory).set_promotes_to("dev", "prod")
    world.dev = await WorkspaceStore(session_factory).get_by_name("dev")
    world.prod = await WorkspaceStore(session_factory).get_by_name("prod")
    return world


async def _dev_case(s, world: World):
    dataset = await get_dataset(s, "dev", "finance")
    [case] = await current_cases(s, dataset)
    return dataset, case


async def _prod_events(s, dataset_id: int) -> list[tuple[str, int | None]]:
    rows = await s.scalars(
        select(CaseEvent).where(CaseEvent.dataset_id == dataset_id).order_by(CaseEvent.id)
    )
    return [(r.kind, r.sync_id) for r in rows.all()]


async def test_plan_needs_target_datasets(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1")])
        await sync(
            s,
            world.dev,
            world.admin_id,
            [],
            dataset="finance-overlays",
            overlays=(overlay("q-1", ["support-bot"]),),
        )
        dataset, case = await _dev_case(s, world)
        with pytest.raises(MissingTargetDataset, match="finance@prod"):
            await plan_promotion(s, world.dev, dataset, case)
        await create_dataset(s, world.prod, "finance")
        with pytest.raises(MissingTargetDataset, match="finance-overlays@prod"):
            await plan_promotion(s, world.dev, dataset, case)
        await create_dataset(s, world.prod, "finance-overlays")
        plan = await plan_promotion(s, world.dev, dataset, case)
    assert plan.target_ref == "finance@prod" and len(plan.overlays) == 1
    assert list(plan.overlay_targets) == ["finance-overlays"]


async def test_execute_promotion_moves_pointer_events_and_overlays(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1", tags=("fy24",))])
        await sync(
            s,
            world.dev,
            world.admin_id,
            [],
            dataset="finance-overlays",
            overlays=(overlay("q-1", ["support-bot"], tags=("client-only",)),),
        )
        await create_dataset(s, world.prod, "finance")
        await create_dataset(s, world.prod, "finance-overlays")
        dataset, case = await _dev_case(s, world)
        plan = await plan_promotion(s, world.dev, dataset, case)
        sync_id = await execute_promotion(s, plan, actor_id=world.akshat_id, event_id=1)
        await s.commit()

        prod = await get_dataset(s, "prod", "finance")
        [promoted] = await current_cases(s, prod)
        assert promoted.content_hash == case.content_hash
        assert promoted.review_state == "approved"
        assert promoted.split_tags == ["fy24", "client-only"]
        assert promoted.effective_evaluators == case.effective_evaluators
        assert await _prod_events(s, prod.id) == [("synced", sync_id), ("approved", None)]
        row = await s.get_one(DatasetSync, sync_id)
        assert row.source == "promotion" and row.detail == {
            "from": "finance@dev",
            "case_id": "q-1",
            "content_hash": case.content_hash,
            "event_id": 1,
            "overlays": 1,
        }

        again = await plan_promotion(s, world.dev, dataset, case)
        await execute_promotion(s, again, actor_id=world.akshat_id, event_id=2)
        await s.commit()
        assert len(await _prod_events(s, prod.id)) == 2

        await sync(s, world.dev, world.admin_id, [incoming("q-1", question="What is EBITDA now?")])
        await sync(s, world.dev, world.admin_id, [], dataset="finance-overlays")
        dataset, case = await _dev_case(s, world)
        plan = await plan_promotion(s, world.dev, dataset, case)
        assert plan.overlays == [] and plan.overlay_targets == {}
        await execute_promotion(s, plan, actor_id=world.akshat_id, event_id=3)
        await s.commit()
        [promoted] = await current_cases(s, prod)
        assert promoted.content_hash == case.content_hash
        assert [k for k, _ in await _prod_events(s, prod.id)] == [
            "synced",
            "approved",
            "synced",
            "approved",
        ]
        overlays = (await s.scalars(select(Overlay))).all()
        assert overlays == []
        [promoted] = await current_cases(s, prod)
        assert promoted.split_tags == []


async def test_twin_status_both_sides(session_factory, world):
    async with session_factory() as s:
        await sync(s, world.dev, world.admin_id, [incoming("q-1"), incoming("q-2")])
        await create_dataset(s, world.prod, "finance")
        dev, _ = (
            await _dev_case(s, world) if False else (await get_dataset(s, "dev", "finance"), None)
        )
        dev_cases = await current_cases(s, dev)
        status = await twin_status(s, world.dev, dev, dev_cases)
        assert status["q-1"]["state"] == "absent" and status["q-1"]["workspace"] == "prod"
        assert status["q-1"]["href"] == "/datasets/finance@prod/cases/q-1"

        q1 = next(c for c in dev_cases if c.case_id == "q-1")
        plan = await plan_promotion(s, world.dev, dev, q1)
        await execute_promotion(s, plan, actor_id=world.akshat_id, event_id=1)
        await s.commit()
        status = await twin_status(s, world.dev, dev, dev_cases)
        assert status["q-1"]["state"] == "same"
        assert status["q-1"]["review"]["state"] == "approved"
        assert status["q-2"]["state"] == "absent"

        prod = await get_dataset(s, "prod", "finance")
        prod_cases = await current_cases(s, prod)
        back = await twin_status(s, world.prod, prod, prod_cases)
        assert back["q-1"]["state"] == "same" and back["q-1"]["workspace"] == "dev"
        assert back["q-1"]["review"]["state"] == "unreviewed"

        await sync(s, world.dev, world.admin_id, [incoming("q-1", question="v2")])
        await s.commit()
        dev_cases = await current_cases(s, dev)
        assert (await twin_status(s, world.dev, dev, dev_cases))["q-1"]["state"] == "differs"
        back = await twin_status(s, world.prod, prod, prod_cases)
        assert back["q-1"]["state"] == "differs"
        assert back["q-1"]["content_hash"] == next(c.content_hash for c in dev_cases)

        await sync(s, world.dev, world.admin_id, [])
        await s.commit()
        back = await twin_status(s, world.prod, prod, prod_cases)
        assert back["q-1"]["state"] == "absent"

        assert await twin_status(s, world.canonical, dev, dev_cases) == {"q-1": None}
