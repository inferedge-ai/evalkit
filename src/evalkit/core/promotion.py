from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from evalkit.core.review import CaseReview, review_summary

__all__ = [
    "MissingTargetDataset",
    "OverlayCopy",
    "PromotionPlan",
    "PromotionTarget",
    "TwinState",
    "twin_state",
    "twin_summary",
]


class MissingTargetDataset(Exception):
    def __init__(self, ref: str):
        self.ref = ref
        super().__init__(
            f"{ref} does not exist; create it before approving (evalkit datasets create)"
        )


class PromotionTarget(Exception):
    def __init__(self, workspace: str, source: str, hint: str):
        self.workspace = workspace
        self.source = source
        super().__init__(f"{workspace} is the promotion target of {source}; {hint}")


@dataclass(frozen=True)
class OverlayCopy:
    source_dataset: str
    agents: tuple[str, ...]
    tags: tuple[str, ...]
    evaluators: dict[str, Any]
    source_file: str | None

    def key(self) -> tuple[str, tuple[str, ...], tuple[str, ...], str]:
        return (self.source_dataset, self.agents, self.tags, repr(sorted(self.evaluators.items())))


@dataclass(frozen=True)
class PromotionPlan:
    source_ref: str
    target_ref: str
    target_workspace_id: int
    target_dataset_id: int
    case_ref: int
    case_id: str
    content_hash: str
    path_tags: list[str]
    source: str | None
    overlays: list[OverlayCopy]
    overlay_targets: dict[str, int]


class TwinState(StrEnum):
    ABSENT = "absent"
    SAME = "same"
    DIFFERS = "differs"


def twin_state(mine: str, other: str | None) -> TwinState:
    if other is None:
        return TwinState.ABSENT
    return TwinState.SAME if mine == other else TwinState.DIFFERS


def twin_summary(
    *,
    workspace: str,
    dataset: str,
    case_id: str,
    mine: str,
    other: str | None,
    review: CaseReview | None,
    my_overlays: list[OverlayCopy],
    other_overlays: list[OverlayCopy],
) -> dict[str, Any]:
    state = twin_state(mine, other)
    return {
        "workspace": workspace,
        "state": state.value,
        "content_hash": other,
        "review": review_summary(review) if review is not None else None,
        "overlays_differ": state is not TwinState.ABSENT
        and {o.key() for o in my_overlays} != {o.key() for o in other_overlays},
        "href": f"/datasets/{dataset}@{workspace}/cases/{case_id}",
    }
