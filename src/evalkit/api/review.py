from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.api.access import ActorDep, StoresDep, TenantStores, require, workspace_ref
from evalkit.api.auth import CurrentUser
from evalkit.api.deps import SessionDep
from evalkit.contracts.enums import SyncSource, WorkspaceRole
from evalkit.core.access import WorkspaceRef, can_review
from evalkit.core.promotion import MissingTargetDataset, PromotionPlan
from evalkit.core.review import (
    CaseReview,
    CaseState,
    CaseVersion,
    NewCaseComment,
    NotReviewable,
    ensure_reviewable,
    group_threads,
    is_reviewable,
    review_summary,
)
from evalkit.core.workspace import parse_slug
from evalkit.db import models
from evalkit.db.cases import current_cases, dataset_by_ref, find_dataset_row, list_datasets
from evalkit.db.promotion import execute_promotion, plan_promotion
from evalkit.store.base import CaseView, DatasetRow, Selection, select_cases

router = APIRouter(prefix="/api", tags=["review"])


class ReviewRequest(BaseModel):
    selection: Selection
    reviewer: str


class DecisionRequest(BaseModel):
    action: Literal["approve", "request_changes", "reject"]
    comment: str | None = None


class CommentRequest(BaseModel):
    body: str
    parent_id: int | None = None


class ResolveRequest(BaseModel):
    resolved: bool


async def _suite(
    stores: TenantStores, session: SessionDep, ref: str
) -> tuple[DatasetRow, list[CaseView]]:
    dataset = await dataset_by_ref(session, stores.tenant_id, ref)
    try:
        ensure_reviewable(dataset)
    except NotReviewable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return dataset, await current_cases(session, dataset)


async def _dataset_workspace(stores: TenantStores, session: SessionDep, ref: str) -> WorkspaceRef:
    dataset = await dataset_by_ref(session, stores.tenant_id, ref)
    return await workspace_ref(stores, dataset.workspace)


async def _not_target(
    stores: TenantStores, ws: WorkspaceRef, slug: str, case_id: str | None
) -> None:
    if not ws.is_target:
        return
    assert ws.promoted_from is not None
    source = await stores.workspaces.get_by_id(ws.promoted_from)
    where = f"{parse_slug(slug).name}@{source.name}"
    what = f"review {case_id} in {where}" if case_id else f"request review in {where}"
    raise HTTPException(status_code=409, detail=f"{slug} is fed by promotion; {what}")


async def _load_case_review(
    stores: TenantStores, session: SessionDep, ref: str, case_id: str
) -> CaseReview:
    _, cases = await _suite(stores, session, ref)
    case = next((c for c in cases if c.case_id == case_id), None)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
    reviews = await stores.review.load(ref, [CaseVersion.of(case)])
    return reviews[case_id]


@router.post("/datasets/{slug}/review-requests")
async def request_review(
    slug: str,
    body: ReviewRequest,
    stores: StoresDep,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    _, cases = await _suite(stores, session, slug)
    ws = await _dataset_workspace(stores, session, slug)
    await _not_target(stores, ws, slug, None)
    reviewers = await stores.workspaces.members(ws.name, WorkspaceRole.REVIEWER)
    if body.reviewer not in {m.username for m in reviewers}:
        raise HTTPException(
            status_code=400, detail=f"{body.reviewer} is not a reviewer of {ws.name}"
        )
    matched = select_cases(cases, body.selection)
    reviews = await stores.review.load(slug, [CaseVersion.of(case) for case in matched])
    pending = {
        case_id
        for case_id, r in reviews.items()
        if r.state() is CaseState.REVIEW_REQUESTED and r.requested_to == body.reviewer
    }
    await stores.review.append(
        [
            r.request_review(user.username, body.reviewer)
            for case_id, r in reviews.items()
            if case_id not in pending
        ]
    )
    return {"requested": sorted(set(reviews) - pending), "already_requested": sorted(pending)}


@router.post("/datasets/{slug}/cases/{case_id}/review")
async def decide(
    slug: str,
    case_id: str,
    body: DecisionRequest,
    stores: StoresDep,
    session: SessionDep,
    user: ActorDep,
) -> dict:
    ws = await _dataset_workspace(stores, session, slug)
    require(can_review(user, ws), "only reviewers of this workspace decide")
    await _not_target(stores, ws, slug, case_id)
    text = (body.comment or "").strip()
    if body.action in ("request_changes", "reject") and not text:
        raise HTTPException(status_code=400, detail=f"{body.action} needs a comment")
    review = await _load_case_review(stores, session, slug, case_id)
    plan: PromotionPlan | None = None
    if body.action == "approve" and ws.promotes_to is not None:
        dataset, cases = await _suite(stores, session, slug)
        case = next(c for c in cases if c.case_id == case_id)
        try:
            plan = await plan_promotion(session, ws, dataset, case)
        except MissingTargetDataset as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    comment = (
        NewCaseComment(
            dataset=slug,
            case_id=case_id,
            content_hash=review.content_hash,
            author=user.username,
            body=text,
        )
        if text
        else None
    )
    if body.action == "approve":
        event = review.approve(user.username)
    elif body.action == "reject":
        event = review.reject(user.username)
    else:
        event = review.request_changes(user.username)
    actor_id = user.id

    async def promote(db: AsyncSession, event_id: int) -> None:
        assert plan is not None
        await execute_promotion(db, plan, actor_id=actor_id, event_id=event_id)

    async def withdraw_from_target(db: AsyncSession, event_id: int) -> None:
        assert ws.promotes_to is not None
        target_ws = await stores.workspaces.get_by_id(ws.promotes_to)
        target = await find_dataset_row(db, ws.tenant_id, target_ws.name, parse_slug(slug).name)
        if target is None:
            return
        case_ref = await db.scalar(select(models.Case.id).where(models.Case.case_id == case_id))
        if case_ref is None:
            return
        active = await db.scalar(
            select(models.DatasetCase).where(
                models.DatasetCase.dataset_id == target.id,
                models.DatasetCase.case_ref == case_ref,
                models.DatasetCase.removed_at.is_(None),
            )
        )
        if active is not None:
            await db.execute(
                update(models.DatasetCase)
                .where(
                    models.DatasetCase.dataset_id == target.id,
                    models.DatasetCase.case_ref == case_ref,
                    models.DatasetCase.removed_at.is_(None),
                )
                .values(removed_at=func.now(), updated_at=func.now())
            )
            db.add(
                models.DatasetSync(
                    dataset_id=target.id,
                    actor_id=actor_id,
                    source=SyncSource.PROMOTION.value,
                    detail={
                        "from": slug,
                        "case_id": case_id,
                        "withdrawn": True,
                        "event_id": event_id,
                    },
                )
            )

    after = None
    if plan is not None:
        after = promote
    elif body.action == "reject" and ws.promotes_to is not None:
        after = withdraw_from_target

    await stores.review.decide(event, comment, after)
    summary = review_summary(await _load_case_review(stores, session, slug, case_id))
    return {**summary, "promoted_to": plan.target_ref if plan is not None else None}


async def _source_dataset(stores: TenantStores, session: SessionDep, slug: str) -> str | None:
    ws = await _dataset_workspace(stores, session, slug)
    if not ws.is_target:
        return None
    assert ws.promoted_from is not None
    source = await stores.workspaces.get_by_id(ws.promoted_from)
    name = parse_slug(slug).name
    found = await find_dataset_row(session, stores.tenant_id, source.name, name)
    return None if found is None else found.ref


@router.get("/datasets/{slug}/cases/{case_id}/events")
async def events(slug: str, case_id: str, stores: StoresDep, session: SessionDep) -> dict:
    await _load_case_review(stores, session, slug, case_id)
    found = await stores.review.history(slug, case_id)
    source = await _source_dataset(stores, session, slug)
    if source is not None:
        promoted = {e.content_hash for e in found}
        found += [
            e for e in await stores.review.history(source, case_id) if e.content_hash in promoted
        ]
        found.sort(key=lambda e: (e.created_at, e.id), reverse=True)
    return {"events": [e.model_dump() for e in found]}


@router.get("/datasets/{slug}/cases/{case_id}/comments")
async def comments(slug: str, case_id: str, stores: StoresDep, session: SessionDep) -> dict:
    await _load_case_review(stores, session, slug, case_id)
    return {"threads": group_threads(await stores.review.comments(slug, case_id))}


@router.post("/datasets/{slug}/cases/{case_id}/comments")
async def add_comment(
    slug: str,
    case_id: str,
    body: CommentRequest,
    stores: StoresDep,
    session: SessionDep,
    user: CurrentUser,
) -> dict:
    review = await _load_case_review(stores, session, slug, case_id)
    if body.parent_id is not None:
        parent = await stores.review.get_comment(body.parent_id)
        if (
            parent is None
            or parse_slug(parent.dataset) != parse_slug(slug)
            or parent.case_id != case_id
        ):
            raise HTTPException(status_code=400, detail="parent comment is not on this case")
        if parent.parent_id is not None:
            raise HTTPException(status_code=400, detail="replies nest one level only")
    if not body.body.strip():
        raise HTTPException(status_code=400, detail="comment body is empty")
    stored = await stores.review.add_comment(
        NewCaseComment(
            dataset=slug,
            case_id=case_id,
            content_hash=review.content_hash,
            parent_id=body.parent_id,
            author=user.username,
            body=body.body.strip(),
        )
    )
    return stored.model_dump()


@router.patch("/datasets/{slug}/cases/{case_id}/comments/{comment_id}")
async def resolve_comment(
    slug: str,
    case_id: str,
    comment_id: int,
    body: ResolveRequest,
    stores: StoresDep,
    session: SessionDep,
    user: ActorDep,
) -> dict:
    comment = await stores.review.get_comment(comment_id)
    if (
        comment is None
        or parse_slug(comment.dataset) != parse_slug(slug)
        or comment.case_id != case_id
    ):
        raise HTTPException(status_code=404, detail=f"unknown comment: {comment_id}")
    ws = await _dataset_workspace(stores, session, slug)
    if comment.author != user.username and not can_review(user, ws):
        raise HTTPException(status_code=403, detail="only a reviewer or the author may resolve")
    return (await stores.review.set_resolved(comment_id, body.resolved)).model_dump()


@router.patch("/review/comments/{comment_id}")
async def resolve_comment_by_id(
    comment_id: int,
    body: ResolveRequest,
    stores: StoresDep,
    session: SessionDep,
    user: ActorDep,
) -> dict:
    comment = await stores.review.get_comment(comment_id)
    if comment is None:
        raise HTTPException(status_code=404, detail=f"unknown comment: {comment_id}")
    return await resolve_comment(
        comment.dataset, comment.case_id, comment_id, body, stores, session, user
    )


QueueState = Literal["review_requested", "changes_requested", "rejected"]


@router.get("/review/queue")
async def queue(
    stores: StoresDep,
    session: SessionDep,
    reviewer: str | None = None,
    state: QueueState = "review_requested",
    workspace: str | None = None,
    dataset: str | None = None,
) -> dict:
    if workspace is not None:
        await workspace_ref(stores, workspace)
    datasets = (
        [await dataset_by_ref(session, stores.tenant_id, dataset)]
        if dataset is not None
        else await list_datasets(session, stores.tenant_id, workspace)
    )
    items: list[dict] = []
    for dataset_row in datasets:
        if not is_reviewable(dataset_row):
            continue
        cases = await current_cases(session, dataset_row)
        reviews = await stores.review.load(
            dataset_row.ref, [CaseVersion.of(case) for case in cases]
        )
        for case in cases:
            r = reviews[case.case_id]
            if r.last_event is None or r.state() is not CaseState(state):
                continue
            if reviewer is not None and r.requested_to != reviewer:
                continue
            question = case.input.get("question")
            items.append(
                {
                    "dataset": dataset_row.ref,
                    "case_id": case.case_id,
                    "requested_to": r.requested_to,
                    "requested_by": r.last_event.actor,
                    "author": r.requested_by,
                    "requested_at": r.last_event.created_at,
                    "reason": r.reason,
                    "input": question if isinstance(question, str) else str(case.input),
                }
            )
    items.sort(key=lambda i: (str(i["requested_at"]), i["dataset"], i["case_id"]), reverse=True)
    return {"items": items}
