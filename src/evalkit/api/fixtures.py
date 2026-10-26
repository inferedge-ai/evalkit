import hashlib
import mimetypes
import re
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response, StreamingResponse

from evalkit.api.access import ActorDep, workspace_ref
from evalkit.api.deps import RunnerDepsDep, SessionDep
from evalkit.api.download import content_disposition
from evalkit.core.blob import fixture_blob_key
from evalkit.db.fixture_blobs import blob_size, known_blobs, record_blob, workspace_fixtures

router = APIRouter(prefix="/api/fixtures", tags=["fixtures"])

FIXTURE_STAGING_PREFIX = "fixtures/staging/"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _sha(value: str) -> str:
    if not _SHA256.match(value):
        raise HTTPException(status_code=400, detail=f"{value!r} is not a lowercase sha256 digest")
    return value


@router.get("")
async def list_fixtures(
    session: SessionDep,
    actor: ActorDep,
    workspace: str | None = None,
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    ws = await workspace_ref(session, workspace or actor.username)
    page = await workspace_fixtures(
        session, ws.id, text=q or None, limit=max(1, min(limit, 200)), offset=max(0, offset)
    )
    return page.model_dump(mode="json")


async def _known(session: SessionDep, sha256: str) -> None:
    if not await known_blobs(session, [_sha(sha256)]):
        raise HTTPException(status_code=404, detail=f"no fixture with sha256 {sha256}")


@router.head("/sha256/{sha256}")
async def head(sha256: str, session: SessionDep) -> Response:
    await _known(session, sha256)
    return Response(status_code=200)


@router.get("/sha256/{sha256}")
async def download(
    sha256: str, deps: RunnerDepsDep, session: SessionDep, name: str | None = None
) -> Response:
    await _known(session, sha256)
    blob = await deps.blob_store.get_stream(fixture_blob_key(sha256))
    media_type = (mimetypes.guess_type(name)[0] if name else None) or "application/octet-stream"
    headers = {"Content-Length": str(blob.size), **(content_disposition(name) if name else {})}
    return StreamingResponse(blob.chunks, media_type=media_type, headers=headers)


@router.put("/sha256/{sha256}")
async def upload(
    sha256: str, request: Request, deps: RunnerDepsDep, session: SessionDep, actor: ActorDep
) -> dict:
    expected = _sha(sha256)
    stored = await blob_size(session, expected)
    if stored is not None:
        return {"sha256": expected, "size": stored}
    hasher = hashlib.sha256()
    size = 0

    async def hashed() -> AsyncIterator[bytes]:
        nonlocal size
        async for chunk in request.stream():
            hasher.update(chunk)
            size += len(chunk)
            yield chunk

    staging = f"{FIXTURE_STAGING_PREFIX}{uuid.uuid4().hex}"
    await deps.blob_store.put_stream(staging, hashed())
    actual = hasher.hexdigest()
    if actual != expected:
        await deps.blob_store.delete_object(staging)
        raise HTTPException(status_code=400, detail=f"bytes hash to {actual}, not {sha256}")
    await deps.blob_store.copy_object(staging, fixture_blob_key(sha256))
    await deps.blob_store.delete_object(staging)
    await record_blob(session, sha256, size, actor.id)
    await session.commit()
    return {"sha256": sha256, "size": size}
