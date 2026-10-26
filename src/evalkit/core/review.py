from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, NamedTuple, Protocol

from pydantic import BaseModel

from evalkit.store.base import CaseView, DatasetRow

__all__ = [
    "DECISIVE_EVENT_KINDS",
    "OPEN_CASE_STATES",
    "CaseComment",
    "CaseEvent",
    "CaseEventKind",
    "CaseReview",
    "CaseState",
    "CaseVersion",
    "NewCaseComment",
    "NewCaseEvent",
    "NoApprovedCases",
    "NotReviewable",
    "ReviewLogProtocol",
    "ensure_reviewable",
    "is_reviewable",
    "group_threads",
    "review_summary",
]


class CaseEventKind(StrEnum):
    SYNCED = "synced"
    REVIEW_REQUESTED = "review_requested"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"


class CaseState(StrEnum):
    DRAFT = "draft"
    REVIEW_REQUESTED = "review_requested"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"


DECISIVE_EVENT_KINDS = frozenset(
    {
        CaseEventKind.REVIEW_REQUESTED,
        CaseEventKind.APPROVED,
        CaseEventKind.CHANGES_REQUESTED,
    }
)

_CASE_STATE_FOR = {
    CaseEventKind.REVIEW_REQUESTED: CaseState.REVIEW_REQUESTED,
    CaseEventKind.APPROVED: CaseState.APPROVED,
    CaseEventKind.CHANGES_REQUESTED: CaseState.CHANGES_REQUESTED,
}

OPEN_CASE_STATES = frozenset({CaseState.REVIEW_REQUESTED, CaseState.CHANGES_REQUESTED})


class CaseVersion(NamedTuple):
    case_id: str
    content_hash: str

    @classmethod
    def of(cls, case: CaseView) -> CaseVersion:
        return cls(case.case_id, case.content_hash)


class NewCaseEvent(BaseModel):
    dataset: str
    case_id: str
    content_hash: str
    kind: CaseEventKind
    actor: str
    target: str | None = None


class CaseEvent(NewCaseEvent):
    id: int
    created_at: datetime


class NewCaseComment(BaseModel):
    dataset: str
    case_id: str
    content_hash: str
    parent_id: int | None = None
    author: str
    body: str


class CaseComment(NewCaseComment):
    id: int
    resolved: bool
    created_at: datetime


@dataclass
class CaseReview:
    dataset: str
    case_id: str
    content_hash: str
    last_event: CaseEvent | None
    requested_to: str | None

    @classmethod
    def unreviewed(cls, dataset: str, case_id: str, content_hash: str) -> CaseReview:
        return cls(
            dataset=dataset,
            case_id=case_id,
            content_hash=content_hash,
            last_event=None,
            requested_to=None,
        )

    @property
    def version(self) -> CaseVersion:
        return CaseVersion(self.case_id, self.content_hash)

    def state(self) -> CaseState:
        if self.last_event is None:
            return CaseState.DRAFT
        return _CASE_STATE_FOR[self.last_event.kind]

    def synced(self, actor: str) -> NewCaseEvent:
        return self._event(CaseEventKind.SYNCED, actor)

    def request_review(self, actor: str, target: str) -> NewCaseEvent:
        return self._event(CaseEventKind.REVIEW_REQUESTED, actor, target)

    def approve(self, actor: str) -> NewCaseEvent:
        return self._event(CaseEventKind.APPROVED, actor)

    def request_changes(self, actor: str) -> NewCaseEvent:
        return self._event(CaseEventKind.CHANGES_REQUESTED, actor)

    def carry_over(self, actor: str, new_hash: str) -> NewCaseEvent | None:
        if self.last_event is None or self.state() not in OPEN_CASE_STATES:
            return None
        return NewCaseEvent(
            dataset=self.dataset,
            case_id=self.case_id,
            content_hash=new_hash,
            kind=CaseEventKind.REVIEW_REQUESTED,
            actor=actor,
            target=self.requested_to or self.last_event.actor,
        )

    def _event(self, kind: CaseEventKind, actor: str, target: str | None = None) -> NewCaseEvent:
        return NewCaseEvent(
            dataset=self.dataset,
            case_id=self.case_id,
            content_hash=self.content_hash,
            kind=kind,
            actor=actor,
            target=target,
        )


def review_summary(review: CaseReview) -> dict[str, Any]:
    state = review.state()
    return {
        "state": state.value,
        "requested_to": review.requested_to if state is CaseState.REVIEW_REQUESTED else None,
        "updated_at": review.last_event.created_at if review.last_event is not None else None,
    }


class ReviewLogProtocol(Protocol):
    async def load(self, dataset: str, versions: list[CaseVersion]) -> dict[str, CaseReview]: ...

    async def latest_hashes(self, dataset: str, case_ids: list[str]) -> dict[str, str]: ...

    async def append(self, events: list[NewCaseEvent]) -> None: ...

    async def decide(
        self,
        event: NewCaseEvent,
        comment: NewCaseComment | None,
        after: Callable[[Any, int], Awaitable[None]] | None = None,
    ) -> None: ...

    async def history(self, dataset: str, case_id: str) -> list[CaseEvent]: ...

    async def comments(self, dataset: str, case_id: str) -> list[CaseComment]: ...

    async def add_comment(self, comment: NewCaseComment) -> CaseComment: ...

    async def get_comment(self, comment_id: int) -> CaseComment | None: ...

    async def set_resolved(self, comment_id: int, resolved: bool) -> CaseComment: ...


def group_threads(comments: list[CaseComment]) -> list[dict[str, Any]]:
    ordered = sorted(comments, key=lambda c: (c.created_at, c.id))
    roots = [c.model_dump() | {"replies": []} for c in ordered if c.parent_id is None]
    by_id = {root["id"]: root for root in roots}
    for comment in ordered:
        if comment.parent_id is not None:
            by_id[comment.parent_id]["replies"].append(comment.model_dump())
    return roots


class NoApprovedCases(Exception):
    def __init__(self, dataset: str):
        super().__init__(
            f"no approved cases in {dataset} match the selection; "
            "pass --include-drafts to run drafts"
        )


class NotReviewable(Exception):
    def __init__(self, dataset: str):
        self.dataset = dataset
        super().__init__(f"{dataset} is not a suite in a shared workspace; review does not apply")


def is_reviewable(dataset: DatasetRow) -> bool:
    return dataset.kind == "suite" and dataset.workspace_is_shared


def ensure_reviewable(dataset: DatasetRow) -> None:
    if not is_reviewable(dataset):
        raise NotReviewable(dataset.ref)
