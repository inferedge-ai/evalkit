from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from evalkit.api.access import ActorDep, ensure_active, require, workspace_ref
from evalkit.api.deps import PgStoreDep, ReviewLogDep, SessionDep
from evalkit.api.experiments import LIST_LIMIT, with_progress
from evalkit.contracts.enums import ExperimentStatus
from evalkit.core.access import can_write
from evalkit.core.review import CaseVersion, is_reviewable, review_summary
from evalkit.db import experiments as db_experiments
from evalkit.db.agents import agent_id_for
from evalkit.db.cases import create_dataset, current_cases, dataset_by_ref, list_datasets, splits
from evalkit.db.promotion import twin_status
from evalkit.db.review import ReviewLog
from evalkit.db.scorecard import case_history
from evalkit.store.base import CaseView, DatasetRow, Selection

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


class CaseSelection(BaseModel):
    selection: Selection
    agent: str | None = None


def _dataset(row: DatasetRow) -> dict:
    return {**row.model_dump(mode="json"), "ref": row.ref}


@router.get("")
async def list_all(session: SessionDep, workspace: str | None = None) -> list[dict]:
    return [_dataset(row) for row in await list_datasets(session, workspace)]


class CreateDataset(BaseModel):
    name: str
    workspace: str


@router.post("")
async def create(body: CreateDataset, session: SessionDep, actor: ActorDep) -> dict:
    ws = await workspace_ref(session, body.workspace)
    require(can_write(actor, ws), f"you cannot create datasets in @{body.workspace}")
    ensure_active(ws)
    row = await create_dataset(session, ws, body.name)
    await session.commit()
    return _dataset(row)


@router.get("/{ref}")
async def get_dataset(ref: str, session: SessionDep) -> dict:
    return _dataset(await dataset_by_ref(session, ref))


async def _with_review(
    session: SessionDep, review_log: ReviewLog, dataset: DatasetRow, cases: list[CaseView]
) -> list[dict]:
    ws = await workspace_ref(session, dataset.workspace)
    twins = await twin_status(session, ws, dataset, cases)
    if not is_reviewable(dataset):
        return [
            case.model_dump(mode="json") | {"review": None, "twin": twins[case.case_id]}
            for case in cases
        ]
    reviews = await review_log.load(dataset.ref, [CaseVersion.of(case) for case in cases])
    return [
        case.model_dump(mode="json")
        | {"review": review_summary(reviews[case.case_id]), "twin": twins[case.case_id]}
        for case in cases
    ]


@router.get("/{ref}/cases")
async def list_cases(
    ref: str,
    session: SessionDep,
    review_log: ReviewLogDep,
    tag: str | None = None,
    q: str | None = None,
) -> list[dict]:
    dataset = await dataset_by_ref(session, ref)
    cases = await current_cases(session, dataset)
    if tag:
        cases = [case for case in cases if tag in case.split_tags]
    if q:
        needle = q.lower()
        cases = [
            case
            for case in cases
            if needle in case.case_id.lower() or needle in str(case.input).lower()
        ]
    return await _with_review(session, review_log, dataset, cases)


@router.get("/{ref}/cases/{case_id}")
async def get_case(ref: str, case_id: str, session: SessionDep, review_log: ReviewLogDep) -> dict:
    dataset = await dataset_by_ref(session, ref)
    case = next((c for c in await current_cases(session, dataset) if c.case_id == case_id), None)
    if case is None:
        raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
    [row] = await _with_review(session, review_log, dataset, [case])
    return row


@router.post("/{ref}/cases/select")
async def select(ref: str, body: CaseSelection, store: PgStoreDep) -> list[dict]:
    cases = await store.cases_for(ref, body.selection, agent=body.agent)
    return [case.model_dump(mode="json") for case in cases]


@router.get("/{ref}/splits")
async def list_splits(ref: str, session: SessionDep) -> list[dict]:
    dataset = await dataset_by_ref(session, ref)
    return [{"name": tag, "count": count} for tag, count in await splits(session, dataset)]


@router.get("/{ref}/experiments")
async def list_experiments(
    ref: str,
    session: SessionDep,
    status: ExperimentStatus | None = None,
    agent: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    dataset = await dataset_by_ref(session, ref)
    found = await db_experiments.list_experiments(
        session,
        dataset_id=dataset.id,
        status=status,
        agent_id=await agent_id_for(session, agent) if agent else None,
        limit=min(limit, LIST_LIMIT),
        offset=offset,
    )
    return await with_progress(session, found)


@router.get("/{ref}/cases/{case_id}/history")
async def case_revisions(ref: str, case_id: str, session: SessionDep) -> dict:
    dataset = await dataset_by_ref(session, ref)
    current = next((c for c in await current_cases(session, dataset) if c.case_id == case_id), None)
    if current is None:
        raise HTTPException(status_code=404, detail=f"unknown case: {case_id}")
    return {
        "case_id": case_id,
        "current": current.model_dump(mode="json"),
        "revisions": [{"content_hash": current.content_hash, "kind": "current"}],
    }


@router.get("/{ref}/cases/{case_id}/scores")
async def case_scores(
    ref: str, case_id: str, session: SessionDep, limit: int = 10, offset: int = 0
) -> dict:
    dataset = await dataset_by_ref(session, ref)
    points = await case_history(session, dataset.id, [case_id], limit=limit, offset=offset)
    return {
        "case_id": case_id,
        "points": [p.model_dump(mode="json") for p in points.get(case_id, [])],
    }


@router.get("/{ref}/case-scores")
async def dataset_case_scores(ref: str, session: SessionDep, experiments: int = 5) -> dict:
    dataset = await dataset_by_ref(session, ref)
    by_case = await case_history(session, dataset.id, None, limit=experiments)
    return {
        case_id: [p.model_dump(mode="json") for p in points] for case_id, points in by_case.items()
    }
