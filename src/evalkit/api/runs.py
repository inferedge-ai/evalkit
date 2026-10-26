import asyncio
import json
import logging
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from posixpath import basename
from typing import IO, Annotated, Literal, cast

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, computed_field
from pydantic_core import from_json
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from evalkit.api.deps import (
    AgentStoreDep,
    RunnerDepsDep,
    SessionDep,
    SessionReaderDep,
    SpanStoreDep,
)
from evalkit.api.download import content_disposition
from evalkit.api.experiments import slug_or_400
from evalkit.contracts.atif import Trajectory, validate_trajectory
from evalkit.contracts.harness import FileEntry, FileList, RunListItem
from evalkit.core.agents import AgentDefinition, explicit, server_may_drive
from evalkit.core.blob import artifact_key, workspace_key
from evalkit.core.costmap import load_cost_map
from evalkit.core.harness import HarnessClient, HarnessFile
from evalkit.core.paths import is_safe_path, is_safe_run_id
from evalkit.core.pull import SCAN_NOTE, list_harness_runs
from evalkit.core.runner import RunnerDeps, case_title
from evalkit.core.trajectory import backfill_step_costs
from evalkit.db.agents import AgentStore
from evalkit.db.runs import (
    AgentRunItem,
    RunSource,
    agent_run,
    list_agent_runs,
    runs_by_harness_ids,
)
from evalkit.db.sessions import DbSessionReader
from evalkit.db.spans import SessionSummary, SpanStore

router = APIRouter(prefix="/api/agents", tags=["agents"])

HARNESS_UNREACHABLE = "harness unreachable"
MAX_PAGE = 200


class BrowserRun(RunListItem):
    status: str | None = None
    experiment_id: int | None = None
    experiment_number: int | None = None
    case_id: str | None = None
    stored: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source(self) -> Literal["app", "eval"]:
        return "eval" if self.experiment_id is not None else "app"


class RunPage(BaseModel):
    runs: list[BrowserRun]
    total: int
    harness_note: str | None = None


def harness_client(deps: RunnerDeps, agent: AgentDefinition) -> HarnessClient:
    return HarnessClient(
        agent, deps.credentials.key_for("agent", agent.ref), transport=deps.harness_transport
    )


async def agent_or_404(agents: AgentStore, agent: str) -> AgentDefinition:
    return await agents.get(explicit(slug_or_400(agent)))


def _sdk_case(item: SessionSummary) -> tuple[str | None, str | None]:
    if item.sdk_root is None:
        return None, None
    case_id = item.sdk_root.get("evalkit.case_id")
    raw_input = item.sdk_root.get("input.value")
    title = case_title(from_json(raw_input, allow_partial=True)) if raw_input else None
    return (str(case_id) if case_id is not None else None), title


def _stored(item: AgentRunItem) -> BrowserRun:
    return BrowserRun(
        run_id=item.harness_run_id,
        title=item.title,
        user_id=item.created_by,
        created_at=item.started_at,
        status=item.status.value,
        experiment_id=item.experiment_id,
        experiment_number=item.experiment_number,
        case_id=item.case_id,
        stored=True,
    )


def _merge(stored: list[BrowserRun], harness: list[RunListItem], limit: int) -> list[BrowserRun]:
    seen = {run.run_id for run in stored}
    extra = [BrowserRun(**run.model_dump()) for run in harness if run.run_id not in seen]
    oldest = datetime.min.replace(tzinfo=UTC)
    merged = sorted([*stored, *extra], key=lambda run: run.created_at or oldest, reverse=True)
    return merged[:limit]


async def resolve_runs(
    record: AgentDefinition,
    deps: RunnerDeps,
    spans: SpanStore,
    session: AsyncSession,
    *,
    source: RunSource = "all",
    since: datetime | None = None,
    until: datetime | None = None,
    query: str | None = None,
    user_id: str | None = None,
    client_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    no_session: bool = False,
    run_ids: list[str] | None = None,
) -> RunPage:
    if run_ids is not None:
        known = await runs_by_harness_ids(session, record.id, run_ids)
        rows = [_stored(known[run_id]) for run_id in run_ids if run_id in known]
        return RunPage(runs=rows, total=len(rows))
    if record.is_otel:
        if no_session:
            items, total = await spans.list_unsessioned(record.id, limit=limit, offset=offset)
        else:
            items, total = await spans.list_sessions(
                record.id,
                query=query,
                limit=limit,
                offset=offset,
                since=since,
                until=until,
                linked=source,
            )
        linked = await runs_by_harness_ids(
            session, record.id, [i.session_id for i in items if i.session_id]
        )
        sessions: list[BrowserRun] = []
        for i in items:
            run = linked.get(i.session_id) if i.session_id else None
            sdk_case_id, sdk_title = _sdk_case(i)
            sessions.append(
                BrowserRun(
                    run_id=i.session_id or i.trace_id,
                    title=(run.title if run else None) or sdk_title or i.title,
                    created_at=i.created_at,
                    user_id=run.created_by if run else None,
                    status=run.status.value if run else None,
                    experiment_id=run.experiment_id if run else None,
                    experiment_number=run.experiment_number if run else None,
                    case_id=(run.case_id if run else None) or sdk_case_id,
                    stored=run is not None,
                )
            )
        return RunPage(runs=sessions, total=total)
    stored_items, stored_total = await list_agent_runs(
        session,
        record.id,
        query=query,
        limit=limit + offset,
        created_by=user_id,
        since=since,
        until=until,
        source=source,
    )
    stored = [_stored(item) for item in stored_items]
    if source == "eval" or not server_may_drive(record):
        return RunPage(runs=stored[offset : offset + limit], total=stored_total)
    client = harness_client(deps, record)
    note: str | None = None
    harness: list[RunListItem] = []
    harness_total = 0

    async def evals(run_ids: list[str]) -> set[str]:
        known = await runs_by_harness_ids(session, record.id, run_ids)
        return {run_id for run_id, item in known.items() if item.experiment_id is not None}

    # Spec "Runs browser": an unreachable harness still lists the stored runs, with a note.
    try:
        listed = await list_harness_runs(
            client,
            wanted=limit + offset,
            since=since,
            until=until,
            query=query,
            user_id=user_id,
            client_id=client_id,
            exclude=evals if source == "app" else None,
        )
        harness, harness_total = listed.runs, listed.total
        if listed.capped:
            note = SCAN_NOTE
    except httpx.HTTPError:
        note = HARNESS_UNREACHABLE
    finally:
        await client.aclose()
    harness_ids = {run.run_id for run in harness}
    if client_id is not None:
        stored = [run for run in stored if run.run_id in harness_ids]
        stored_total = len(stored)
    overlap = sum(1 for run in stored if run.run_id in harness_ids)
    merged = _merge(stored, harness, limit + offset)[offset:]
    return RunPage(
        runs=merged,
        total=max(stored_total + harness_total - overlap, len(merged) + offset),
        harness_note=note,
    )


@router.get("/{agent}/runs")
async def list_runs(
    agent: str,
    deps: RunnerDepsDep,
    agents: AgentStoreDep,
    spans: SpanStoreDep,
    session: SessionDep,
    query: str | None = None,
    user_id: str | None = None,
    client_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
    no_session: bool = False,
    source: RunSource = "all",
    since: datetime | None = None,
    until: datetime | None = None,
    run_id: Annotated[list[str] | None, Query()] = None,
) -> dict:
    page = await resolve_runs(
        await agent_or_404(agents, agent),
        deps,
        spans,
        session,
        source=source,
        since=since,
        until=until,
        query=query,
        user_id=user_id,
        client_id=client_id,
        limit=min(limit, MAX_PAGE),
        offset=offset,
        no_session=no_session,
        run_ids=run_id,
    )
    return page.model_dump(
        mode="json", exclude={"harness_note"} if page.harness_note is None else None
    )


async def read_trajectory(
    record: AgentDefinition,
    run_id: str,
    deps: RunnerDeps,
    sessions: DbSessionReader,
    trajectory_key: str | None,
    client: HarnessClient | None,
) -> Trajectory:
    if trajectory_key:
        return validate_trajectory(json.loads(await deps.blob_store.get_object(trajectory_key)))
    if record.is_otel:
        result = await sessions.read(record.ref, run_id)
        if result is None or result.trajectory is None:
            raise HTTPException(status_code=404, detail="no spans for this session")
        return validate_trajectory(result.trajectory)
    if client is not None:
        try:
            return await client.trajectory(run_id)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=exc.response.status_code, detail="harness error"
            ) from exc
    raise HTTPException(status_code=404, detail="run has no stored trajectory")


async def fetch_trajectory(
    record: AgentDefinition,
    run_id: str,
    deps: RunnerDeps,
    sessions: DbSessionReader,
    session: AsyncSession,
) -> Trajectory:
    stored = await agent_run(session, record.id, run_id)
    key = stored.trajectory_key if stored is not None else None
    client = harness_client(deps, record) if not key and server_may_drive(record) else None
    try:
        return await read_trajectory(record, run_id, deps, sessions, key, client)
    finally:
        if client is not None:
            await client.aclose()


@router.get("/{agent}/runs/{run_id}/trajectory")
async def live_trajectory(
    agent: str,
    run_id: str,
    deps: RunnerDepsDep,
    agents: AgentStoreDep,
    sessions: SessionReaderDep,
    session: SessionDep,
) -> dict:
    _run_id_or_400(run_id)
    record = await agent_or_404(agents, agent)
    trajectory = await fetch_trajectory(record, run_id, deps, sessions, session)
    # Price any step the harness left uncosted so the cost view matches the
    # scorecard gauge (see backfill_step_costs).
    backfill_step_costs(trajectory, load_cost_map(deps.cfg.cost_map))
    return trajectory.model_dump(exclude_none=True)


NOT_ON_HARNESS = "not found on the harness: wrong path, or the harness no longer has it"


def _run_id_or_400(run_id: str) -> str:
    if not is_safe_run_id(run_id):
        raise HTTPException(status_code=400, detail="run id must be one plain path segment")
    return run_id


def _path_or_400(path: str) -> str:
    if not is_safe_path(path):
        raise HTTPException(status_code=400, detail="path must have no empty, '.' or '..' segments")
    return path.lstrip("/")


def _harness_status(exc: httpx.HTTPStatusError) -> HTTPException:
    if exc.response.status_code == 404:
        return HTTPException(status_code=404, detail=NOT_ON_HARNESS)
    return HTTPException(status_code=exc.response.status_code, detail="harness error")


async def _stored_files(deps: RunnerDeps, record: AgentDefinition, run_id: str) -> dict[str, int]:
    prefix = artifact_key(record.id, run_id, "")
    return {
        key.removeprefix(prefix): size for key, size in await deps.blob_store.list_objects(prefix)
    }


async def _snapshot(deps: RunnerDeps, record: AgentDefinition, run_id: str) -> dict[str, FileEntry]:
    key = workspace_key(record.id, run_id)
    if not await deps.blob_store.object_exists(key):
        return {}
    tree = FileList.model_validate_json(await deps.blob_store.get_object(key))
    return {entry.path: entry for entry in tree.files}


async def _listing(
    deps: RunnerDeps, record: AgentDefinition, run_id: str
) -> tuple[dict[str, FileEntry], set[str]]:
    stored = await _stored_files(deps, record, run_id)
    snapshot = await _snapshot(deps, record, run_id)
    if snapshot or stored or not server_may_drive(record):
        kept = {path: FileEntry(path=path, size=size) for path, size in stored.items()}
        return {**kept, **snapshot}, set(stored)
    client = harness_client(deps, record)
    try:
        files = await client.list_files(run_id)
    except httpx.HTTPStatusError as exc:
        raise _harness_status(exc) from exc
    finally:
        await client.aclose()
    return {entry.path: entry for entry in files.files}, set()


@router.get("/{agent}/runs/{run_id}/files")
async def list_files(agent: str, run_id: str, deps: RunnerDepsDep, agents: AgentStoreDep) -> dict:
    _run_id_or_400(run_id)
    record = await agent_or_404(agents, agent)
    listing, stored = await _listing(deps, record, run_id)
    return {
        "files": [
            {**entry.model_dump(), "stored": path in stored}
            for path, entry in sorted(listing.items())
        ]
    }


LOGGER = logging.getLogger(__name__)

# A single oversized file is refused before the response starts; once headers
# are out an error can only truncate the zip.
MAX_FILE_BYTES = 512 * 1024 * 1024


class _ChunkSink:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> int:
        self.buffer.extend(data)
        return len(data)

    def flush(self) -> None:
        pass

    def drain(self) -> bytes:
        chunk = bytes(self.buffer)
        self.buffer.clear()
        return chunk


# zipfile writes to the unseekable sink with data descriptors, so every entry
# is flushed to the client as soon as it is compressed and the archive never
# sits in memory as a whole. A fetch failure after the headers are out cannot
# become a status code; re-raising drops the connection so the browser shows
# a failed download rather than a quietly smaller zip.
async def _zip_stream(
    entries: list[tuple[str, str]],
    fetch: Callable[[str], Awaitable[AsyncIterator[bytes]]],
    close: Callable[[], Awaitable[None]],
) -> AsyncIterator[bytes]:
    sink = _ChunkSink()
    try:
        with zipfile.ZipFile(cast(IO[bytes], sink), "w", zipfile.ZIP_DEFLATED) as archive:
            for arcname, path in entries:
                try:
                    chunks = await fetch(path)
                except httpx.HTTPStatusError as exc:
                    LOGGER.warning(
                        "zip aborted: harness returned %s for %s", exc.response.status_code, path
                    )
                    raise
                with archive.open(arcname, "w", force_zip64=True) as entry:
                    async for chunk in chunks:
                        await asyncio.to_thread(entry.write, chunk)
                        yield sink.drain()
                yield sink.drain()
        yield sink.drain()
    finally:
        await close()


def _zip_entries(
    listing: dict[str, int], folder: str, fallback_name: str
) -> tuple[str, list[tuple[str, str]]]:
    prefix = f"{folder}/" if folder else ""
    root = basename(folder) if folder else fallback_name
    selected = sorted(p for p in listing if p.lstrip("/").startswith(prefix) and is_safe_path(p))
    if not selected:
        raise HTTPException(status_code=404, detail=f"no files under {folder or '/'}")
    largest = max(selected, key=lambda p: listing[p])
    if listing[largest] > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"{largest} is {listing[largest]} bytes; zip entries stop at {MAX_FILE_BYTES}",
        )
    return root, [(f"{root}/{p.lstrip('/')[len(prefix) :]}", p) for p in selected]


def _zip_response(
    root: str,
    entries: list[tuple[str, str]],
    fetch: Callable[[str], Awaitable[AsyncIterator[bytes]]],
    close: Callable[[], Awaitable[None]],
) -> StreamingResponse:
    return StreamingResponse(
        _zip_stream(entries, fetch, close),
        media_type="application/zip",
        headers=content_disposition(f"{root}.zip"),
    )


async def _noop() -> None:
    return None


@router.get("/{agent}/runs/{run_id}/zip")
async def zip_files(
    agent: str, run_id: str, deps: RunnerDepsDep, agents: AgentStoreDep, path: str = ""
) -> StreamingResponse:
    _run_id_or_400(run_id)
    folder = path.strip("/")
    if folder:
        _path_or_400(folder)
    agent_def = await agent_or_404(agents, agent)
    listing, stored = await _listing(deps, agent_def, run_id)
    drivable = server_may_drive(agent_def)
    if not drivable:
        listing = {p: entry for p, entry in listing.items() if p in stored}
    sizes = {p: entry.size for p, entry in listing.items()}
    root, entries = _zip_entries(sizes, folder, run_id)
    prefix = artifact_key(agent_def.id, run_id, "")

    async def stored_chunks(p: str) -> AsyncIterator[bytes]:
        return (await deps.blob_store.get_stream(f"{prefix}{p}")).chunks

    if all(p in stored for _, p in entries):
        return _zip_response(root, entries, stored_chunks, _noop)
    client = harness_client(deps, agent_def)

    async def fetch(p: str) -> AsyncIterator[bytes]:
        if p in stored:
            return await stored_chunks(p)
        return (await client.stream_file(run_id, p)).chunks

    return _zip_response(root, entries, fetch, client.aclose)


async def _closing(file: HarnessFile, client: HarnessClient) -> AsyncIterator[bytes]:
    try:
        async for chunk in file.chunks:
            yield chunk
    finally:
        await client.aclose()


@router.get("/{agent}/runs/{run_id}/files/{path:path}")
async def get_file(
    agent: str,
    run_id: str,
    path: str,
    deps: RunnerDepsDep,
    agents: AgentStoreDep,
    download: bool = False,
) -> StreamingResponse:
    _run_id_or_400(run_id)
    path = _path_or_400(path)
    headers = content_disposition(basename(path)) if download else None
    record = await agent_or_404(agents, agent)
    key = artifact_key(record.id, run_id, path)
    if await deps.blob_store.object_exists(key):
        blob = await deps.blob_store.get_stream(key)
        return StreamingResponse(
            blob.chunks,
            media_type="application/octet-stream",
            headers={"Content-Length": str(blob.size), **(headers or {})},
        )
    if server_may_drive(record):
        client = harness_client(deps, record)
        async with AsyncExitStack() as cleanup:
            cleanup.push_async_callback(client.aclose)
            try:
                file = await client.stream_file(run_id, path)
            except httpx.HTTPStatusError as exc:
                raise _harness_status(exc) from exc
            cleanup.pop_all()
        length = {"Content-Length": str(file.size)} if file.size is not None else {}
        return StreamingResponse(
            _closing(file, client),
            media_type="application/octet-stream",
            headers={**length, **(headers or {})},
        )
    raise HTTPException(status_code=404, detail="no such file")
