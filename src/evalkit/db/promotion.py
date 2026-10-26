from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.contracts.enums import SyncSource
from evalkit.core.access import WorkspaceRef
from evalkit.core.promotion import (
    MissingTargetDataset,
    OverlayCopy,
    PromotionPlan,
    twin_summary,
)
from evalkit.core.review import CaseEvent, CaseEventKind, CaseReview
from evalkit.db import models
from evalkit.db.cases import find_dataset_row
from evalkit.db.review_state import latest_events
from evalkit.db.workspaces import load_ref
from evalkit.store.base import CaseView, DatasetRow


async def overlays_extending(
    session: AsyncSession, workspace_id: int, case_refs: list[int]
) -> dict[int, list[OverlayCopy]]:
    if not case_refs:
        return {}
    rows = (
        await session.execute(
            select(models.Overlay, models.Dataset.name)
            .join(models.Dataset, models.Dataset.id == models.Overlay.source_dataset_id)
            .where(
                models.Dataset.workspace_id == workspace_id,
                models.Overlay.target_case.in_(case_refs),
            )
            .order_by(models.Dataset.name)
        )
    ).all()
    out: dict[int, list[OverlayCopy]] = {}
    for overlay, source in rows:
        out.setdefault(overlay.target_case, []).append(
            OverlayCopy(
                source_dataset=source,
                agents=tuple(overlay.agents),
                tags=tuple(overlay.tags),
                evaluators=overlay.evaluators,
                source_file=overlay.source_file,
            )
        )
    return out


async def _workspace(session: AsyncSession, workspace_id: int) -> WorkspaceRef:
    return await load_ref(session, await session.get_one(models.Workspace, workspace_id))


async def _username(session: AsyncSession, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    return await session.scalar(select(models.User.username).where(models.User.id == user_id))


async def _placement(
    session: AsyncSession, dataset_id: int, case_ref: int
) -> tuple[list[str], str | None]:
    row = (
        await session.execute(
            select(models.DatasetCase.path_tags, models.DatasetCase.source).where(
                models.DatasetCase.dataset_id == dataset_id,
                models.DatasetCase.case_ref == case_ref,
            )
        )
    ).one()
    return list(row[0]), row[1]


async def plan_promotion(
    session: AsyncSession, source_ws: WorkspaceRef, dataset: DatasetRow, case: CaseView
) -> PromotionPlan:
    assert source_ws.promotes_to is not None
    target_ws = await load_ref(
        session, await session.get_one(models.Workspace, source_ws.promotes_to)
    )
    target = await find_dataset_row(session, target_ws.name, dataset.name)
    if target is None or target.kind != "suite":
        raise MissingTargetDataset(f"{dataset.name}@{target_ws.name}")
    overlays = (await overlays_extending(session, source_ws.id, [case.case_ref])).get(
        case.case_ref, []
    )
    overlay_targets: dict[str, int] = {}
    for name in sorted({o.source_dataset for o in overlays}):
        found = await find_dataset_row(session, target_ws.name, name)
        if found is None:
            raise MissingTargetDataset(f"{name}@{target_ws.name}")
        overlay_targets[name] = found.id
    path_tags, source = await _placement(session, dataset.id, case.case_ref)
    return PromotionPlan(
        source_ref=dataset.ref,
        target_ref=target.ref,
        target_workspace_id=target_ws.id,
        target_dataset_id=target.id,
        case_ref=case.case_ref,
        case_id=case.case_id,
        content_hash=case.content_hash,
        path_tags=path_tags,
        source=source,
        overlays=overlays,
        overlay_targets=overlay_targets,
    )


async def execute_promotion(
    session: AsyncSession, plan: PromotionPlan, *, actor_id: int, event_id: int
) -> int:
    sync = models.DatasetSync(
        dataset_id=plan.target_dataset_id,
        actor_id=actor_id,
        source=SyncSource.PROMOTION.value,
        detail={
            "from": plan.source_ref,
            "case_id": plan.case_id,
            "content_hash": plan.content_hash,
            "event_id": event_id,
            "overlays": len(plan.overlays),
        },
    )
    session.add(sync)
    await session.flush()
    current = (
        await session.execute(
            select(models.DatasetCase.content_hash, models.DatasetCase.removed_at).where(
                models.DatasetCase.dataset_id == plan.target_dataset_id,
                models.DatasetCase.case_ref == plan.case_ref,
            )
        )
    ).one_or_none()
    if current is None or current[0] != plan.content_hash or current[1] is not None:
        statement = insert(models.DatasetCase).values(
            dataset_id=plan.target_dataset_id,
            case_ref=plan.case_ref,
            content_hash=plan.content_hash,
            path_tags=plan.path_tags,
            source=plan.source,
            removed_at=None,
        )
        await session.execute(
            statement.on_conflict_do_update(
                index_elements=["dataset_id", "case_ref"],
                set_={
                    "content_hash": statement.excluded.content_hash,
                    "path_tags": statement.excluded.path_tags,
                    "source": statement.excluded.source,
                    "removed_at": None,
                    "updated_at": func.now(),
                },
            )
        )
        for kind, sync_id in (
            (CaseEventKind.SYNCED, sync.id),
            (CaseEventKind.APPROVED, None),
        ):
            session.add(
                models.CaseEvent(
                    dataset_id=plan.target_dataset_id,
                    case_ref=plan.case_ref,
                    content_hash=plan.content_hash,
                    kind=kind.value,
                    sync_id=sync_id,
                    actor_id=actor_id,
                )
            )
            await session.flush()
    await session.execute(
        delete(models.Overlay).where(
            models.Overlay.target_case == plan.case_ref,
            models.Overlay.source_dataset_id.in_(
                select(models.Dataset.id).where(
                    models.Dataset.workspace_id == plan.target_workspace_id
                )
            ),
        )
    )
    for overlay in plan.overlays:
        session.add(
            models.Overlay(
                source_dataset_id=plan.overlay_targets[overlay.source_dataset],
                target_case=plan.case_ref,
                agents=list(overlay.agents),
                tags=list(overlay.tags),
                evaluators=overlay.evaluators,
                source_file=overlay.source_file,
            )
        )
    await session.flush()
    return sync.id


async def _review_of(
    session: AsyncSession, dataset: DatasetRow, case_ref: int, case_id: str, content_hash: str
) -> CaseReview:
    events = await latest_events(session, dataset.id, {case_ref: content_hash})
    review = CaseReview.unreviewed(dataset.ref, case_id, content_hash)
    row = events.decisive.get(case_ref)
    if row is not None:
        actor = await _username(session, row.actor_id)
        assert actor is not None
        review.last_event = CaseEvent(
            id=row.id,
            dataset=dataset.ref,
            case_id=case_id,
            content_hash=row.content_hash,
            kind=CaseEventKind(row.kind),
            actor=actor,
            target=await _username(session, row.target_id),
            created_at=row.created_at,
        )
    review.requested_to = await _username(session, events.requested_to.get(case_ref))
    return review


async def twin_status(
    session: AsyncSession, ws: WorkspaceRef, dataset: DatasetRow, cases: list[CaseView]
) -> dict[str, dict[str, Any] | None]:
    other_id = ws.promotes_to if ws.promotes_to is not None else ws.promoted_from
    if other_id is None or dataset.kind != "suite" or not cases:
        return {case.case_id: None for case in cases}
    other_ws = await _workspace(session, other_id)
    other = await find_dataset_row(session, other_ws.name, dataset.name)
    refs = [case.case_ref for case in cases]
    theirs: dict[int, tuple[str, datetime | None]] = {}
    if other is not None:
        theirs = {
            ref: (content_hash, removed_at)
            for ref, content_hash, removed_at in (
                await session.execute(
                    select(
                        models.DatasetCase.case_ref,
                        models.DatasetCase.content_hash,
                        models.DatasetCase.removed_at,
                    ).where(
                        models.DatasetCase.dataset_id == other.id,
                        models.DatasetCase.case_ref.in_(refs),
                    )
                )
            ).all()
        }
    mine_overlays = await overlays_extending(session, ws.id, refs)
    their_overlays = await overlays_extending(session, other_ws.id, refs)
    out: dict[str, dict[str, Any] | None] = {}
    for case in cases:
        found = theirs.get(case.case_ref)
        other_hash = found[0] if found is not None and found[1] is None else None
        review = (
            await _review_of(session, other, case.case_ref, case.case_id, other_hash)
            if other is not None and other_hash is not None
            else None
        )
        out[case.case_id] = twin_summary(
            workspace=other_ws.name,
            dataset=dataset.name,
            case_id=case.case_id,
            mine=case.content_hash,
            other=other_hash,
            review=review,
            my_overlays=mine_overlays.get(case.case_ref, []),
            other_overlays=their_overlays.get(case.case_ref, []),
        )
    return out
