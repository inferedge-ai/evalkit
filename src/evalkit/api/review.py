from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.api.access import ActorDep, require, workspace_ref
from evalkit.api.auth import CurrentUser
from evalkit.api.deps import ReviewLogDep, SessionDep
from evalkit.contracts.enums import WorkspaceRole
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
from evalkit.db.cases import current_cases, dataset_by_ref, list_datasets
from evalkit.db.promotion import execute_promotion, plan_promotion
from evalkit.db.review import ReviewLog
from evalkit.db.workspaces import WorkspaceStore
from evalkit.store.base import CaseView, DatasetRow, Selection, select_cases

router = APIRouter(prefix="/api", tags=["review"])


class ReviewRequest(BaseModel):
    selection: Selection
    reviewer: str


class DecisionRequest(BaseModel):
    action: Literal["approve", "request_changes"]
    comment: str | None = None


class CommentRequest(BaseModel):
    body: str
    parent_id: int | None = None


class ResolveRequest(BaseModel):
    resolved: bool


async def _suite(session: SessionDep, ref: str) -> tuple[DatasetRow, list[CaseView]]:
    dataset = await dataset_by_ref(session, ref)
    try:
        ensure_reviewable(dataset)
    except NotReviewable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return dataset, await current_cases(session, dataset)


async def _dataset_workspace(session: SessionDep, ref: str) -> WorkspaceRef:
    return await workspace_ref(session, (await dataset_by_ref(session, ref)).workspace)


async def _not_target(request: Request, ws: WorkspaceRef, slug: str, case_id: str | None) -> None:
    if not ws.is_target:
        return
    assert ws.promoted_from is not None
    source = await WorkspaceStore(request.app.state.session_factory).get_by_id(ws.promoted_from)
    where = f"{parse_slug(slug).name}@{source.name}"
    what = f"review {case_id} in {where}" if case_id else f"request review in {where}"
    raise HTTPException(status_code=409, detail=f"{slug} is fed by promotion; {what}")


async def _load_case_review(
    session: SessionDep, review_log: ReviewLog, ref: str, case_id: str
) -> CaseReview:
    _, cases = await _suite(session, ref)
    case = next((c for c in cases if c.case_id == case_id), None)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
    reviews = await review_log.load(ref, [CaseVersion.of(case)])
    return reviews[case_id]


@router.post("/datasets/{slug}/review-requests")
async def request_review(
    slug: str,
    body: ReviewRequest,
    review_log: ReviewLogDep,
    session: SessionDep,
    request: Request,
    user: CurrentUser,
) -> dict:
    _, cases = await _suite(session, slug)
    ws = await _dataset_workspace(session, slug)
    await _not_target(request, ws, slug, None)
    reviewers = await WorkspaceStore(request.app.state.session_factory).members(
        ws.name, WorkspaceRole.REVIEWER
    )
    if body.reviewer not in {m.username for m in reviewers}:
        raise HTTPException(
            status_code=400, detail=f"{body.reviewer} is not a reviewer of {ws.name}"
        )
    matched = select_cases(cases, body.selection)
    reviews = await review_log.load(slug, [CaseVersion.of(case) for case in matched])
    await review_log.append(
        [r.request_review(user.username, body.reviewer) for r in reviews.values()]
    )
    return {"requested": sorted(reviews)}


@router.post("/datasets/{slug}/cases/{case_id}/review")
async def decide(
    slug: str,
    case_id: str,
    body: DecisionRequest,
    review_log: ReviewLogDep,
    session: SessionDep,
    request: Request,
    user: ActorDep,
) -> dict:
    ws = await _dataset_workspace(session, slug)
    require(can_review(user, ws), "only reviewers of this workspace decide")
    await _not_target(request, ws, slug, case_id)
    text = (body.comment or "").strip()
    if body.action == "request_changes" and not text:
        raise HTTPException(status_code=400, detail="request_changes needs a comment")
    review = await _load_case_review(session, review_log, slug, case_id)
    plan: PromotionPlan | None = None
    if body.action == "approve" and ws.promotes_to is not None:
        dataset, cases = await _suite(session, slug)
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
    event = (
        review.approve(user.username)
        if body.action == "approve"
        else review.request_changes(user.username)
    )
    actor_id = user.id

    async def promote(db: AsyncSession, event_id: int) -> None:
        assert plan is not None
        await execute_promotion(db, plan, actor_id=actor_id, event_id=event_id)

    await review_log.decide(event, comment, promote if plan is not None else None)
    summary = review_summary(await _load_case_review(session, review_log, slug, case_id))
    return {**summary, "promoted_to": plan.target_ref if plan is not None else None}


@router.get("/datasets/{slug}/cases/{case_id}/events")
async def events(slug: str, case_id: str, session: SessionDep, review_log: ReviewLogDep) -> dict:
    await _load_case_review(session, review_log, slug, case_id)
    return {"events": [e.model_dump() for e in await review_log.history(slug, case_id)]}


@router.get("/datasets/{slug}/cases/{case_id}/comments")
async def comments(slug: str, case_id: str, session: SessionDep, review_log: ReviewLogDep) -> dict:
    await _load_case_review(session, review_log, slug, case_id)
    return {"threads": group_threads(await review_log.comments(slug, case_id))}


@router.post("/datasets/{slug}/cases/{case_id}/comments")
async def add_comment(
    slug: str,
    case_id: str,
    body: CommentRequest,
    session: SessionDep,
    review_log: ReviewLogDep,
    user: CurrentUser,
) -> dict:
    review = await _load_case_review(session, review_log, slug, case_id)
    if body.parent_id is not None:
        parent = await review_log.get_comment(body.parent_id)
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
    stored = await review_log.add_comment(
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
    review_log: ReviewLogDep,
    session: SessionDep,
    user: ActorDep,
) -> dict:
    comment = await review_log.get_comment(comment_id)
    if (
        comment is None
        or parse_slug(comment.dataset) != parse_slug(slug)
        or comment.case_id != case_id
    ):
        raise HTTPException(status_code=404, detail=f"unknown comment: {comment_id}")
    ws = await _dataset_workspace(session, slug)
    if comment.author != user.username and not can_review(user, ws):
        raise HTTPException(status_code=403, detail="only a reviewer or the author may resolve")
    return (await review_log.set_resolved(comment_id, body.resolved)).model_dump()


QueueState = Literal["review_requested", "changes_requested"]


@router.get("/review/queue")
async def queue(
    session: SessionDep,
    review_log: ReviewLogDep,
    reviewer: str | None = None,
    state: QueueState = "review_requested",
    workspace: str | None = None,
) -> dict:
    items: list[dict] = []
    for dataset in await list_datasets(session, workspace):
        if not is_reviewable(dataset):
            continue
        cases = await current_cases(session, dataset)
        reviews = await review_log.load(dataset.ref, [CaseVersion.of(case) for case in cases])
        for case in cases:
            r = reviews[case.case_id]
            if r.last_event is None or r.state() is not CaseState(state):
                continue
            if reviewer is not None and r.requested_to != reviewer:
                continue
            question = case.input.get("question")
            items.append(
                {
                    "dataset": dataset.ref,
                    "case_id": case.case_id,
                    "requested_to": r.requested_to,
                    "requested_by": r.last_event.actor,
                    "requested_at": r.last_event.created_at,
                    "input": question if isinstance(question, str) else str(case.input),
                }
            )
    items.sort(key=lambda i: (str(i["requested_at"]), i["dataset"], i["case_id"]), reverse=True)
    return {"items": items}
