from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import delete, func, select, true, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.contracts.enums import DatasetKind, SyncSource
from evalkit.core.access import WorkspaceRef
from evalkit.core.overlays import OverlayBlock, apply_overlay_blocks
from evalkit.core.review import CaseEventKind, CaseState
from evalkit.core.workspace import Reference, parse_slug
from evalkit.db import models
from evalkit.db.review_state import latest_events
from evalkit.db.users import check_name
from evalkit.store.base import (
    CarriedReview,
    CaseFile,
    CaseView,
    DatasetRow,
    DatasetSyncView,
    IncomingCase,
    IncomingOverlay,
    SyncResult,
    UnknownDataset,
)

Change = Literal["created", "patched", "unchanged"]


def _sync_view(row: models.DatasetSync, actor: str) -> DatasetSyncView:
    return DatasetSyncView(
        id=row.id,
        source=row.source,
        detail=row.detail,
        actor=row.detail.get("actor") or actor,
        created_at=row.created_at,
    )


async def _last_syncs(session: AsyncSession, dataset_ids: list[int]) -> dict[int, DatasetSyncView]:
    if not dataset_ids:
        return {}
    rows = (
        await session.execute(
            select(models.DatasetSync, models.User.username)
            .join(models.User, models.User.id == models.DatasetSync.actor_id)
            .where(models.DatasetSync.dataset_id.in_(dataset_ids))
            .distinct(models.DatasetSync.dataset_id)
            .order_by(models.DatasetSync.dataset_id, models.DatasetSync.id.desc())
        )
    ).all()
    return {row.dataset_id: _sync_view(row, actor) for row, actor in rows}


def _dataset_query():
    return select(models.Dataset, models.Workspace).join(
        models.Workspace, models.Workspace.id == models.Dataset.workspace_id
    )


async def _rows(session: AsyncSession, query) -> list[DatasetRow]:
    found = (await session.execute(query)).all()
    syncs = await _last_syncs(session, [d.id for d, _ in found])
    return [
        DatasetRow(
            id=d.id,
            workspace=w.name,
            workspace_is_shared=w.owner_user_id is None,
            name=d.name,
            kind=d.kind,
            last_sync=syncs.get(d.id),
        )
        for d, w in found
    ]


async def get_dataset(session: AsyncSession, workspace: str, name: str) -> DatasetRow:
    rows = await _rows(
        session,
        _dataset_query().where(models.Workspace.name == workspace, models.Dataset.name == name),
    )
    if not rows:
        raise UnknownDataset(f"{name}@{workspace}")
    return rows[0]


async def dataset_by_ref(session: AsyncSession, ref: str) -> DatasetRow:
    parsed = parse_slug(ref)
    return await get_dataset(session, parsed.workspace, parsed.name)


async def find_dataset_row(session: AsyncSession, workspace: str, name: str) -> DatasetRow | None:
    rows = await _rows(
        session,
        _dataset_query().where(models.Workspace.name == workspace, models.Dataset.name == name),
    )
    return rows[0] if rows else None


async def list_datasets(session: AsyncSession, workspace: str | None = None) -> list[DatasetRow]:
    query = _dataset_query().where(models.Dataset.archived_at.is_(None))
    if workspace is not None:
        query = query.where(models.Workspace.name == workspace)
    return await _rows(
        session,
        query.order_by(
            models.Workspace.owner_user_id.is_not(None), models.Workspace.name, models.Dataset.name
        ),
    )


async def case_ref_for(session: AsyncSession, case_id: str) -> int:
    await session.execute(
        insert(models.Case)
        .values(case_id=case_id)
        .on_conflict_do_nothing(index_elements=["case_id"])
    )
    found = await session.scalar(select(models.Case.id).where(models.Case.case_id == case_id))
    assert found is not None
    return found


async def case_refs_for(session: AsyncSession, case_ids: list[str]) -> dict[str, int]:
    if not case_ids:
        return {}
    unique = list(dict.fromkeys(case_ids))
    await session.execute(
        insert(models.Case)
        .values([{"case_id": case_id} for case_id in unique])
        .on_conflict_do_nothing(index_elements=["case_id"])
    )
    rows = await session.execute(
        select(models.Case.case_id, models.Case.id).where(models.Case.case_id.in_(unique))
    )
    return {case_id: ref for case_id, ref in rows.all()}


async def store_versions(session: AsyncSession, versions: list[tuple[int, IncomingCase]]) -> None:
    if not versions:
        return
    await session.execute(
        insert(models.CaseVersion)
        .values(
            [
                {
                    "case_ref": case_ref,
                    "content_hash": case.content_hash,
                    "input": case.input,
                    "expected": case.expected,
                    "evaluators": case.evaluators,
                    "tags": case.tags,
                }
                for case_ref, case in versions
            ]
        )
        .on_conflict_do_nothing(index_elements=["case_ref", "content_hash"])
    )
    files = [
        {
            "case_ref": case_ref,
            "content_hash": case.content_hash,
            "key": file.key,
            "sha256": file.sha256,
            "context": file.context,
            "position": position,
        }
        for case_ref, case in versions
        for position, file in enumerate(case.files)
    ]
    if files:
        await session.execute(
            insert(models.CaseVersionFile)
            .values(files)
            .on_conflict_do_nothing(index_elements=["case_ref", "content_hash", "key"])
        )


async def store_version(session: AsyncSession, case_ref: int, case: IncomingCase) -> None:
    await store_versions(session, [(case_ref, case)])


async def _carry_or_reset(
    session: AsyncSession,
    dataset_id: int,
    changed: dict[int, tuple[str, str]],
    actor_id: int,
) -> tuple[dict[int, int], set[int]]:
    if not changed:
        return {}, set()
    events = await latest_events(
        session, dataset_id, {ref: previous for ref, (previous, _) in changed.items()}
    )
    carried: dict[int, int] = {}
    now_draft: set[int] = set()
    for case_ref, (_, current) in changed.items():
        decisive = events.decisive.get(case_ref)
        if decisive is None:
            continue
        if decisive.kind == CaseEventKind.APPROVED:
            now_draft.add(case_ref)
            continue
        target = events.requested_to.get(case_ref) or decisive.actor_id
        session.add(
            models.CaseEvent(
                dataset_id=dataset_id,
                case_ref=case_ref,
                content_hash=current,
                kind=CaseEventKind.REVIEW_REQUESTED.value,
                actor_id=actor_id,
                target_id=target,
            )
        )
        carried[case_ref] = target
    return carried, now_draft


class DatasetExists(ValueError):
    def __init__(self, ref: str):
        super().__init__(f"{ref} already exists")


async def create_dataset(session: AsyncSession, workspace: WorkspaceRef, name: str) -> DatasetRow:
    check_name(name)
    if await find_dataset_row(session, workspace.name, name) is not None:
        raise DatasetExists(f"{name}@{workspace.name}")
    session.add(models.Dataset(workspace_id=workspace.id, name=name, kind=DatasetKind.SUITE.value))
    await session.flush()
    return await get_dataset(session, workspace.name, name)


async def ensure_dataset(
    session: AsyncSession, workspace: WorkspaceRef, name: str, kind: DatasetKind
) -> int:
    await session.execute(
        insert(models.Dataset)
        .values(workspace_id=workspace.id, name=name, kind=kind.value)
        .on_conflict_do_nothing(index_elements=["workspace_id", "name"])
    )
    found = await session.scalar(
        select(models.Dataset.id).where(
            models.Dataset.workspace_id == workspace.id, models.Dataset.name == name
        )
    )
    assert found is not None
    return found


async def _usernames(session: AsyncSession, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = await session.execute(
        select(models.User.id, models.User.username).where(models.User.id.in_(ids))
    )
    return {uid: name for uid, name in rows.all()}


async def _replace_overlays(
    session: AsyncSession, dataset_id: int, overlays: list[IncomingOverlay]
) -> int:
    await session.execute(
        delete(models.Overlay).where(models.Overlay.source_dataset_id == dataset_id)
    )
    targets = {
        case_id: ref
        for case_id, ref in (
            await session.execute(
                select(models.Case.case_id, models.Case.id).where(
                    models.Case.case_id.in_({o.target_case_id for o in overlays})
                )
            )
        ).all()
    }
    applied = 0
    for overlay in overlays:
        target = targets.get(overlay.target_case_id)
        if target is None:
            continue
        session.add(
            models.Overlay(
                source_dataset_id=dataset_id,
                target_case=target,
                agents=overlay.agents,
                tags=overlay.tags,
                evaluators=overlay.evaluators,
                source_file=overlay.source_file,
            )
        )
        applied += 1
    return applied


async def sync_dataset(
    session: AsyncSession,
    *,
    workspace: WorkspaceRef,
    dataset: str,
    kind: DatasetKind,
    cases: list[IncomingCase],
    overlays: list[IncomingOverlay],
    full: bool,
    actor_id: int,
    source: SyncSource,
    detail: dict,
) -> SyncResult:
    dataset_id = await ensure_dataset(session, workspace, dataset, kind)
    sync = models.DatasetSync(
        dataset_id=dataset_id, actor_id=actor_id, source=source.value, detail=dict(detail)
    )
    session.add(sync)
    await session.flush()
    result = SyncResult(sync_id=sync.id, dataset_id=dataset_id)
    refs = await case_refs_for(session, [case.case_id for case in cases])
    by_ref = {refs[case.case_id]: case for case in cases}
    incoming = set(by_ref)
    await store_versions(session, list(by_ref.items()))
    existing = {
        row.case_ref: row
        for row in (
            await session.scalars(
                select(models.DatasetCase).where(models.DatasetCase.dataset_id == dataset_id)
            )
        ).all()
    }
    upserts: list[dict] = []
    synced: list[models.CaseEvent] = []
    changed: dict[int, tuple[str, str]] = {}
    for case_ref, case in by_ref.items():
        row = existing.get(case_ref)
        same_version = (
            row is not None and row.content_hash == case.content_hash and row.removed_at is None
        )
        if same_version:
            result.unchanged += 1
        else:
            (result.created if row is None else result.patched).append(case.case_id)
            synced.append(
                models.CaseEvent(
                    dataset_id=dataset_id,
                    case_ref=case_ref,
                    content_hash=case.content_hash,
                    kind=CaseEventKind.SYNCED.value,
                    sync_id=sync.id,
                    actor_id=actor_id,
                )
            )
            if row is not None and row.content_hash != case.content_hash:
                changed[case_ref] = (row.content_hash, case.content_hash)
        if (
            same_version
            and row is not None
            and list(row.path_tags) == case.path_tags
            and row.source == case.source
        ):
            continue
        upserts.append(
            {
                "dataset_id": dataset_id,
                "case_ref": case_ref,
                "content_hash": case.content_hash,
                "path_tags": case.path_tags,
                "source": case.source,
                "removed_at": None,
            }
        )
    if upserts:
        statement = insert(models.DatasetCase).values(upserts)
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
    session.add_all(synced)
    carried_to, now_draft = await _carry_or_reset(session, dataset_id, changed, actor_id)
    carried = [
        (case.case_id, carried_to[case_ref])
        for case_ref, case in by_ref.items()
        if case_ref in carried_to
    ]
    result.now_draft = [case.case_id for case_ref, case in by_ref.items() if case_ref in now_draft]
    if full:
        stale = (
            await session.execute(
                select(models.DatasetCase.case_ref, models.Case.case_id)
                .join(models.Case, models.Case.id == models.DatasetCase.case_ref)
                .where(
                    models.DatasetCase.dataset_id == dataset_id,
                    models.DatasetCase.removed_at.is_(None),
                    models.DatasetCase.case_ref.not_in(incoming) if incoming else true(),
                )
            )
        ).all()
        if stale:
            await session.execute(
                update(models.DatasetCase)
                .where(
                    models.DatasetCase.dataset_id == dataset_id,
                    models.DatasetCase.case_ref.in_([ref for ref, _ in stale]),
                )
                .values(removed_at=datetime.now(UTC))
            )
            result.removed = sorted(case_id for _, case_id in stale)
    result.overlays = await _replace_overlays(session, dataset_id, overlays)
    names = await _usernames(session, {target for _, target in carried})
    result.carried = [CarriedReview(case_id=c, reviewer=names.get(t, "")) for c, t in carried]
    sync.detail = {
        **sync.detail,
        "counts": {
            "created": len(result.created),
            "patched": len(result.patched),
            "removed": len(result.removed),
            "unchanged": result.unchanged,
        },
    }
    return result


async def _overlay_blocks(
    session: AsyncSession, workspace_id: int, case_refs: list[int]
) -> dict[int, list[OverlayBlock]]:
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
        )
    ).all()
    blocks: dict[int, list[OverlayBlock]] = {}
    for overlay, source in rows:
        blocks.setdefault(overlay.target_case, []).append(
            OverlayBlock(
                source=source,
                agents=list(overlay.agents),
                tags=list(overlay.tags),
                evaluators=overlay.evaluators,
                source_file=overlay.source_file,
            )
        )
    return blocks


async def files_for(
    session: AsyncSession, versions: list[tuple[int, str]]
) -> dict[tuple[int, str], list[CaseFile]]:
    if not versions:
        return {}
    rows = (
        await session.scalars(
            select(models.CaseVersionFile)
            .where(
                tuple_(models.CaseVersionFile.case_ref, models.CaseVersionFile.content_hash).in_(
                    versions
                )
            )
            .order_by(models.CaseVersionFile.position)
        )
    ).all()
    out: dict[tuple[int, str], list[CaseFile]] = {}
    for row in rows:
        out.setdefault((row.case_ref, row.content_hash), []).append(
            CaseFile(key=row.key, sha256=row.sha256, context=row.context)
        )
    return out


async def case_views(
    session: AsyncSession,
    workspace_id: int,
    versions: list[tuple[int, str]],
    *,
    agent: str | None,
    dataset_id: int,
    review_dataset_id: int | None = None,
) -> list[CaseView]:
    if not versions:
        return []
    rows = (
        await session.execute(
            select(models.CaseVersion, models.Case.case_id)
            .join(models.Case, models.Case.id == models.CaseVersion.case_ref)
            .where(
                tuple_(models.CaseVersion.case_ref, models.CaseVersion.content_hash).in_(versions)
            )
        )
    ).all()
    by_version = {(v.case_ref, v.content_hash): (v, case_id) for v, case_id in rows}
    placement = {
        ref: (list(path_tags), source)
        for ref, path_tags, source in (
            await session.execute(
                select(
                    models.DatasetCase.case_ref,
                    models.DatasetCase.path_tags,
                    models.DatasetCase.source,
                ).where(
                    models.DatasetCase.dataset_id == dataset_id,
                    models.DatasetCase.case_ref.in_([ref for ref, _ in versions]),
                )
            )
        ).all()
    }
    blocks = await _overlay_blocks(session, workspace_id, [ref for ref, _ in versions])
    files = await files_for(session, versions)
    states: dict[int, str] = {}
    if review_dataset_id is not None:
        events = await latest_events(session, review_dataset_id, dict(versions))
        states = {
            ref: (
                CaseState(events.decisive[ref].kind).value
                if ref in events.decisive
                else CaseState.DRAFT.value
            )
            for ref, _ in versions
        }
    views: list[CaseView] = []
    for ref, content_hash in versions:
        version, case_id = by_version[(ref, content_hash)]
        case_blocks = blocks.get(ref, [])
        overlay_tags = [tag for block in case_blocks for tag in block.tags]
        path_tags, source = placement.get(ref, ([], None))
        tags = list(dict.fromkeys([*version.tags, *path_tags]))
        views.append(
            CaseView(
                case_ref=ref,
                case_id=case_id,
                content_hash=content_hash,
                input=version.input,
                expected=version.expected,
                evaluators=version.evaluators,
                effective_evaluators=apply_overlay_blocks(version.evaluators, case_blocks, agent),
                tags=tags,
                split_tags=list(dict.fromkeys([*tags, *overlay_tags])),
                source=source,
                files=files.get((ref, content_hash), []),
                review_state=states.get(ref),
            )
        )
    return sorted(views, key=lambda v: v.case_id)


async def current_versions(session: AsyncSession, dataset_id: int) -> list[tuple[int, str]]:
    rows = await session.execute(
        select(models.DatasetCase.case_ref, models.DatasetCase.content_hash).where(
            models.DatasetCase.dataset_id == dataset_id, models.DatasetCase.removed_at.is_(None)
        )
    )
    return [(ref, h) for ref, h in rows.all()]


async def current_cases(
    session: AsyncSession, dataset: DatasetRow, *, agent: str | None = None
) -> list[CaseView]:
    workspace_id = await session.scalar(
        select(models.Dataset.workspace_id).where(models.Dataset.id == dataset.id)
    )
    assert workspace_id is not None
    reviewed = dataset.workspace_is_shared and dataset.kind == DatasetKind.SUITE
    return await case_views(
        session,
        workspace_id,
        await current_versions(session, dataset.id),
        agent=agent,
        dataset_id=dataset.id,
        review_dataset_id=dataset.id if reviewed else None,
    )


async def splits(session: AsyncSession, dataset: DatasetRow) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for view in await current_cases(session, dataset):
        for tag in view.split_tags:
            counts[tag] = counts.get(tag, 0) + 1
    return sorted(counts.items())


async def workspace_case_index(
    session: AsyncSession, workspace_id: int, *, exclude_dataset: int | None = None
) -> dict[str, str]:
    query = (
        select(models.Case.case_id, models.Dataset.name)
        .join(models.DatasetCase, models.DatasetCase.case_ref == models.Case.id)
        .join(models.Dataset, models.Dataset.id == models.DatasetCase.dataset_id)
        .where(
            models.Dataset.workspace_id == workspace_id,
            models.DatasetCase.removed_at.is_(None),
        )
    )
    if exclude_dataset is not None:
        query = query.where(models.Dataset.id != exclude_dataset)
    return {case_id: name for case_id, name in (await session.execute(query)).all()}


def dataset_ref(row: DatasetRow) -> Reference:
    return Reference(row.name, row.workspace)
