from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.contracts.enums import RemovalReason, SyncSource
from evalkit.core.access import WorkspaceRef
from evalkit.core.promotion import (
    MissingTargetDataset,
    OverlayCopy,
    PromotionPlan,
    SourceCaseChanged,
    twin_summary,
)
from evalkit.core.review import CaseEvent, CaseEventKind, CaseReview
from evalkit.db import models
from evalkit.db.cases import check_writer, current_cases, find_dataset_row, soft_remove
from evalkit.db.review_state import latest_events
from evalkit.db.workspaces import load_ref
from evalkit.store.base import (
    CaseView,
    DatasetRow,
    NotPromoted,
    Restored,
    TargetCases,
    WrittenByExtension,
)


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
    found = await _plan_or_blocker(session, source_ws, dataset, case)
    if isinstance(found, Exception):
        raise found
    return found


async def _plan_or_blocker(
    session: AsyncSession, source_ws: WorkspaceRef, dataset: DatasetRow, case: CaseView
) -> PromotionPlan | MissingTargetDataset | WrittenByExtension:
    assert source_ws.promotes_to is not None
    target_ws = await load_ref(
        session, await session.get_one(models.Workspace, source_ws.promotes_to)
    )
    target = await find_dataset_row(session, target_ws.tenant_id, target_ws.name, dataset.name)
    if target is None or target.kind != "suite":
        return MissingTargetDataset(f"{dataset.name}@{target_ws.name}")
    if target.written_by_extension:
        return WrittenByExtension(target.ref)
    overlays = (await overlays_extending(session, source_ws.id, [case.case_ref])).get(
        case.case_ref, []
    )
    overlay_targets: dict[str, int] = {}
    for name in sorted({o.source_dataset for o in overlays}):
        overlay_target = await find_dataset_row(session, target_ws.tenant_id, target_ws.name, name)
        if overlay_target is None:
            return MissingTargetDataset(f"{name}@{target_ws.name}")
        overlay_targets[name] = overlay_target.id
    path_tags, source = await _placement(session, dataset.id, case.case_ref)
    return PromotionPlan(
        source_ref=dataset.ref,
        source_dataset_id=dataset.id,
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
    session: AsyncSession,
    plan: PromotionPlan,
    *,
    actor_id: int,
    event_id: int,
    approver_id: int | None = None,
) -> int:
    await check_writer(session, plan.source_dataset_id, plan.source_ref, SyncSource.PROMOTION)
    source_hash = await session.scalar(
        select(models.DatasetCase.content_hash).where(
            models.DatasetCase.dataset_id == plan.source_dataset_id,
            models.DatasetCase.case_ref == plan.case_ref,
            models.DatasetCase.removed_at.is_(None),
        )
    )
    if source_hash != plan.content_hash:
        raise SourceCaseChanged(plan.source_ref, plan.case_id)
    await check_writer(session, plan.target_dataset_id, plan.target_ref, SyncSource.PROMOTION)
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
    source_dataset = await session.get_one(models.Dataset, plan.source_dataset_id)
    target_dataset = await session.get_one(models.Dataset, plan.target_dataset_id)
    target_dataset.readme = source_dataset.readme
    target_dataset.meta = source_dataset.meta
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
                    actor_id=(
                        approver_id
                        if kind is CaseEventKind.APPROVED and approver_id is not None
                        else actor_id
                    ),
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


async def remove_from_target(
    session: AsyncSession,
    source_ws: WorkspaceRef,
    dataset: str,
    hashes: dict[int, str | None],
    *,
    removal_reason: RemovalReason,
    actor_id: int,
    event_id: int | None = None,
) -> TargetCases | None:
    if source_ws.promotes_to is None or not hashes:
        return None
    target_ws = await _workspace(session, source_ws.promotes_to)
    target = await find_dataset_row(session, target_ws.tenant_id, target_ws.name, dataset)
    if target is None or target.kind != "suite" or target.written_by_extension:
        return None
    await check_writer(session, target.id, target.ref, SyncSource.PROMOTION)
    rows = (
        await session.execute(
            select(
                models.DatasetCase.case_ref, models.DatasetCase.content_hash, models.Case.case_id
            )
            .join(models.Case, models.Case.id == models.DatasetCase.case_ref)
            .where(
                models.DatasetCase.dataset_id == target.id,
                models.DatasetCase.removed_at.is_(None),
                models.DatasetCase.case_ref.in_(list(hashes)),
            )
        )
    ).all()
    gone = {
        case_ref: case_id
        for case_ref, content_hash, case_id in rows
        if hashes[case_ref] in (None, content_hash)
    }
    if not gone:
        return None
    removed = sorted(gone.values())
    session.add(
        models.DatasetSync(
            dataset_id=target.id,
            actor_id=actor_id,
            source=SyncSource.PROMOTION.value,
            detail={
                "from": f"{dataset}@{source_ws.name}",
                "removed": removed,
                "removal_reason": removal_reason.value,
                **({"event_id": event_id} if event_id is not None else {}),
            },
        )
    )
    await soft_remove(session, target.id, list(gone))
    still_held = select(models.DatasetCase.case_ref).where(
        models.DatasetCase.removed_at.is_(None),
        models.DatasetCase.dataset_id.in_(
            select(models.Dataset.id).where(models.Dataset.workspace_id == target_ws.id)
        ),
    )
    await session.execute(
        delete(models.Overlay).where(
            models.Overlay.target_case.in_(list(gone)),
            models.Overlay.target_case.not_in(still_held),
            models.Overlay.source_dataset_id.in_(
                select(models.Dataset.id).where(models.Dataset.workspace_id == target_ws.id)
            ),
        )
    )
    await session.flush()
    return TargetCases(target=target.ref, cases=removed)


async def promote_restored(
    session: AsyncSession,
    source_ws: WorkspaceRef,
    dataset: str,
    case_refs: list[int],
    actor_id: int,
) -> Restored:
    if source_ws.promotes_to is None or not case_refs:
        return Restored()
    source = await find_dataset_row(session, source_ws.tenant_id, source_ws.name, dataset)
    if source is None or source.kind != "suite":
        return Restored()
    cases = {c.case_ref: c for c in await current_cases(session, source) if c.case_ref in case_refs}
    events = await latest_events(
        session, source.id, {ref: case.content_hash for ref, case in cases.items()}
    )
    restored = Restored()
    promoted: list[str] = []
    target_ref = ""
    for ref, case in sorted(cases.items(), key=lambda item: item[1].case_id):
        event = events.decisive.get(ref)
        if event is None or event.kind != CaseEventKind.APPROVED.value:
            continue
        found = await _plan_or_blocker(session, source_ws, source, case)
        if isinstance(found, Exception):
            reason = (
                f"{found.ref} does not exist"
                if isinstance(found, MissingTargetDataset)
                else str(found)
            )
            restored.not_promoted.append(NotPromoted(case=case.case_id, reason=reason))
            continue
        await execute_promotion(
            session, found, actor_id=actor_id, event_id=event.id, approver_id=event.actor_id
        )
        promoted.append(case.case_id)
        target_ref = found.target_ref
    if promoted:
        restored.promoted_to = TargetCases(target=target_ref, cases=promoted)
    return restored


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
    other = await find_dataset_row(session, other_ws.tenant_id, other_ws.name, dataset.name)
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
