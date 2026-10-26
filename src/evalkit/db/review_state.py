from dataclasses import dataclass, field

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.core.review import DECISIVE_EVENT_KINDS, CaseEventKind
from evalkit.db import models


@dataclass
class LatestEvents:
    decisive: dict[int, models.CaseEvent] = field(default_factory=dict)
    requested_to: dict[int, int] = field(default_factory=dict)


async def _latest(
    session: AsyncSession, dataset_id: int, versions: dict[int, str], kinds: list[str]
) -> dict[int, models.CaseEvent]:
    rows = await session.scalars(
        select(models.CaseEvent)
        .where(
            models.CaseEvent.dataset_id == dataset_id,
            tuple_(models.CaseEvent.case_ref, models.CaseEvent.content_hash).in_(
                list(versions.items())
            ),
            models.CaseEvent.kind.in_(kinds),
        )
        .distinct(models.CaseEvent.case_ref)
        .order_by(
            models.CaseEvent.case_ref,
            models.CaseEvent.created_at.desc(),
            models.CaseEvent.id.desc(),
        )
    )
    return {row.case_ref: row for row in rows.all()}


async def latest_events(
    session: AsyncSession, dataset_id: int, versions: dict[int, str]
) -> LatestEvents:
    if not versions:
        return LatestEvents()
    decisive = await _latest(session, dataset_id, versions, [k.value for k in DECISIVE_EVENT_KINDS])
    requests = await _latest(session, dataset_id, versions, [CaseEventKind.REVIEW_REQUESTED.value])
    return LatestEvents(
        decisive=decisive,
        requested_to={
            ref: event.target_id for ref, event in requests.items() if event.target_id is not None
        },
    )
