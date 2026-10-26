from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.db import models
from evalkit.store.base import FixtureUse, FixtureUsePage


async def known_blobs(session: AsyncSession, shas: list[str]) -> set[str]:
    if not shas:
        return set()
    rows = await session.scalars(
        select(models.FixtureBlob.sha256).where(models.FixtureBlob.sha256.in_(shas))
    )
    return set(rows.all())


async def blob_size(session: AsyncSession, sha256: str) -> int | None:
    return await session.scalar(
        select(models.FixtureBlob.size).where(models.FixtureBlob.sha256 == sha256)
    )


async def record_blob(
    session: AsyncSession, sha256: str, size: int, uploaded_by: int | None
) -> None:
    await session.execute(
        insert(models.FixtureBlob)
        .values(sha256=sha256, size=size, uploaded_by=uploaded_by)
        .on_conflict_do_nothing(index_elements=["sha256"])
    )


def _uses(workspace_id: int, text: str | None) -> Select:
    query = (
        select(
            models.CaseVersionFile.key,
            models.CaseVersionFile.sha256,
            models.FixtureBlob.size,
            func.array_agg(func.distinct(models.Dataset.name)).label("datasets"),
            models.User.username,
        )
        .join(
            models.DatasetCase,
            (models.DatasetCase.case_ref == models.CaseVersionFile.case_ref)
            & (models.DatasetCase.content_hash == models.CaseVersionFile.content_hash),
        )
        .join(models.Dataset, models.Dataset.id == models.DatasetCase.dataset_id)
        .join(models.FixtureBlob, models.FixtureBlob.sha256 == models.CaseVersionFile.sha256)
        .outerjoin(models.User, models.User.id == models.FixtureBlob.uploaded_by)
        .where(models.Dataset.workspace_id == workspace_id, models.DatasetCase.removed_at.is_(None))
        .group_by(
            models.CaseVersionFile.key,
            models.CaseVersionFile.sha256,
            models.FixtureBlob.size,
            models.User.username,
        )
    )
    if text:
        query = query.where(models.CaseVersionFile.key.icontains(text, autoescape=True))
    return query


async def workspace_fixtures(
    session: AsyncSession,
    workspace_id: int,
    *,
    text: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> FixtureUsePage:
    uses = _uses(workspace_id, text)
    total = await session.scalar(select(func.count()).select_from(uses.subquery()))
    rows = await session.execute(
        uses.order_by(models.CaseVersionFile.key, models.CaseVersionFile.sha256)
        .limit(limit)
        .offset(offset)
    )
    return FixtureUsePage(
        items=[
            FixtureUse(
                key=key,
                sha256=sha256,
                size=size,
                datasets=sorted(datasets),
                uploaded_by=uploaded_by,
            )
            for key, sha256, size, datasets, uploaded_by in rows.all()
        ],
        total=total or 0,
    )
