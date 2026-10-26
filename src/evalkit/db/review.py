from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.core.review import (
    CaseComment,
    CaseEvent,
    CaseEventKind,
    CaseReview,
    CaseVersion,
    NewCaseComment,
    NewCaseEvent,
)
from evalkit.core.workspace import Reference, slug
from evalkit.db import models
from evalkit.db.cases import dataset_by_ref
from evalkit.db.review_state import latest_events

AfterDecision = Callable[[AsyncSession, int], Awaitable[None]]


async def _case_refs(session: AsyncSession, case_ids: list[str]) -> dict[str, int]:
    rows = await session.execute(
        select(models.Case.case_id, models.Case.id).where(models.Case.case_id.in_(case_ids))
    )
    return {case_id: ref for case_id, ref in rows.all()}


async def _user_ids(session: AsyncSession, names: set[str]) -> dict[str, int]:
    rows = await session.execute(
        select(models.User.username, models.User.id).where(models.User.username.in_(names))
    )
    return {name: uid for name, uid in rows.all()}


async def _usernames(session: AsyncSession, ids: set[int]) -> dict[int, str]:
    rows = await session.execute(
        select(models.User.id, models.User.username).where(models.User.id.in_(ids))
    )
    return {uid: name for uid, name in rows.all()}


class ReviewLog:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._session_factory = session_factory

    async def _events(
        self, session: AsyncSession, dataset: str, case_id: str, rows: list[models.CaseEvent]
    ) -> list[CaseEvent]:
        users = {r.actor_id for r in rows} | {r.target_id for r in rows if r.target_id}
        names = await _usernames(session, users)
        return [
            CaseEvent(
                id=r.id,
                dataset=dataset,
                case_id=case_id,
                content_hash=r.content_hash,
                kind=CaseEventKind(r.kind),
                actor=names[r.actor_id],
                target=names[r.target_id] if r.target_id else None,
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def _comment(
        self, session: AsyncSession, row: models.CaseComment, dataset: str, case_id: str
    ) -> CaseComment:
        names = await _usernames(session, {row.author_id})
        return CaseComment(
            id=row.id,
            dataset=dataset,
            case_id=case_id,
            content_hash=row.content_hash,
            parent_id=row.parent_id,
            author=names[row.author_id],
            body=row.body,
            resolved=row.resolved,
            created_at=row.created_at,
        )

    async def load(self, dataset: str, versions: list[CaseVersion]) -> dict[str, CaseReview]:
        if not versions:
            return {}
        reviews = {
            v.case_id: CaseReview.unreviewed(dataset, v.case_id, v.content_hash) for v in versions
        }
        async with self._session_factory() as session:
            dataset_id = (await dataset_by_ref(session, dataset)).id
            refs = await _case_refs(session, [v.case_id for v in versions])
            ids = {ref: case_id for case_id, ref in refs.items()}
            latest = await latest_events(
                session,
                dataset_id,
                {refs[v.case_id]: v.content_hash for v in versions if v.case_id in refs},
            )
            for ref, row in latest.decisive.items():
                [event] = await self._events(session, dataset, ids[ref], [row])
                reviews[ids[ref]].last_event = event
            names = await _usernames(session, set(latest.requested_to.values()))
        for ref, target in latest.requested_to.items():
            reviews[ids[ref]].requested_to = names[target]
        return reviews

    async def latest_hashes(self, dataset: str, case_ids: list[str]) -> dict[str, str]:
        if not case_ids:
            return {}
        async with self._session_factory() as session:
            dataset_id = (await dataset_by_ref(session, dataset)).id
            rows = await session.execute(
                select(models.Case.case_id, models.DatasetCase.content_hash)
                .join(models.Case, models.Case.id == models.DatasetCase.case_ref)
                .where(
                    models.DatasetCase.dataset_id == dataset_id,
                    models.Case.case_id.in_(case_ids),
                )
            )
            return {case_id: content_hash for case_id, content_hash in rows.all()}

    async def _add_events(
        self, session: AsyncSession, events: list[NewCaseEvent]
    ) -> list[models.CaseEvent]:
        refs = await _case_refs(session, [e.case_id for e in events])
        users = await _user_ids(
            session, {e.actor for e in events} | {e.target for e in events if e.target}
        )
        rows: list[models.CaseEvent] = []
        for event in events:
            dataset_id = (await dataset_by_ref(session, event.dataset)).id
            row = models.CaseEvent(
                dataset_id=dataset_id,
                case_ref=refs[event.case_id],
                content_hash=event.content_hash,
                kind=event.kind.value,
                actor_id=users[event.actor],
                target_id=users[event.target] if event.target else None,
            )
            session.add(row)
            await session.flush()
            rows.append(row)
        return rows

    async def _comment_row(
        self, session: AsyncSession, comment: NewCaseComment
    ) -> models.CaseComment:
        dataset_id = (await dataset_by_ref(session, comment.dataset)).id
        refs = await _case_refs(session, [comment.case_id])
        users = await _user_ids(session, {comment.author})
        return models.CaseComment(
            dataset_id=dataset_id,
            case_ref=refs[comment.case_id],
            content_hash=comment.content_hash,
            parent_id=comment.parent_id,
            author_id=users[comment.author],
            body=comment.body,
        )

    async def append(self, events: list[NewCaseEvent]) -> None:
        if not events:
            return
        async with self._session_factory() as session:
            await self._add_events(session, events)
            await session.commit()

    async def decide(
        self,
        event: NewCaseEvent,
        comment: NewCaseComment | None,
        after: AfterDecision | None = None,
    ) -> None:
        async with self._session_factory() as session:
            if comment is not None:
                session.add(await self._comment_row(session, comment))
            [row] = await self._add_events(session, [event])
            if after is not None:
                await after(session, row.id)
            await session.commit()

    async def history(self, dataset: str, case_id: str) -> list[CaseEvent]:
        async with self._session_factory() as session:
            dataset_id = (await dataset_by_ref(session, dataset)).id
            rows = (
                await session.scalars(
                    select(models.CaseEvent)
                    .join(models.Case, models.Case.id == models.CaseEvent.case_ref)
                    .where(
                        models.CaseEvent.dataset_id == dataset_id, models.Case.case_id == case_id
                    )
                    .order_by(models.CaseEvent.created_at.desc(), models.CaseEvent.id.desc())
                )
            ).all()
            return await self._events(session, dataset, case_id, list(rows))

    async def comments(self, dataset: str, case_id: str) -> list[CaseComment]:
        async with self._session_factory() as session:
            dataset_id = (await dataset_by_ref(session, dataset)).id
            rows = (
                await session.scalars(
                    select(models.CaseComment)
                    .join(models.Case, models.Case.id == models.CaseComment.case_ref)
                    .where(
                        models.CaseComment.dataset_id == dataset_id,
                        models.Case.case_id == case_id,
                    )
                    .order_by(models.CaseComment.created_at.asc(), models.CaseComment.id.asc())
                )
            ).all()
            return [await self._comment(session, row, dataset, case_id) for row in rows]

    async def add_comment(self, comment: NewCaseComment) -> CaseComment:
        async with self._session_factory() as session:
            row = await self._comment_row(session, comment)
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return await self._comment(session, row, comment.dataset, comment.case_id)

    async def _describe(self, session: AsyncSession, row: models.CaseComment) -> CaseComment:
        name, workspace = (
            await session.execute(
                select(models.Dataset.name, models.Workspace.name)
                .join(models.Workspace, models.Workspace.id == models.Dataset.workspace_id)
                .where(models.Dataset.id == row.dataset_id)
            )
        ).one()
        case_id = await session.scalar(
            select(models.Case.case_id).where(models.Case.id == row.case_ref)
        )
        assert case_id is not None
        return await self._comment(session, row, slug(Reference(name, workspace)), case_id)

    async def get_comment(self, comment_id: int) -> CaseComment | None:
        async with self._session_factory() as session:
            row = await session.get(models.CaseComment, comment_id)
            return None if row is None else await self._describe(session, row)

    async def set_resolved(self, comment_id: int, resolved: bool) -> CaseComment:
        async with self._session_factory() as session:
            await session.execute(
                update(models.CaseComment)
                .where(models.CaseComment.id == comment_id)
                .values(resolved=resolved)
            )
            await session.commit()
            row = await session.get(models.CaseComment, comment_id)
            assert row is not None
            return await self._describe(session, row)
