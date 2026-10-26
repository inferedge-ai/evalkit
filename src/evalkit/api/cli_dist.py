import hashlib
import html
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import FileResponse, HTMLResponse

from evalkit.version import version_from_wheel_filename

router = APIRouter(prefix="/api/cli", tags=["cli"])


@dataclass(frozen=True)
class ServedWheel:
    path: Path
    filename: str
    version: str
    sha256: str
    size: int


def load_wheel(wheel_dir: Path | None, running_version: str) -> ServedWheel | None:
    if wheel_dir is None:
        return None
    wheels = sorted(wheel_dir.glob("*.whl"))
    if not wheels:
        return None
    if len(wheels) > 1:
        raise RuntimeError(f"{wheel_dir} holds {len(wheels)} wheels; expected one")
    path = wheels[0]
    wheel_version = version_from_wheel_filename(path.name)
    if wheel_version != running_version:
        raise RuntimeError(
            f"wheel {path.name} is {wheel_version} but the server is {running_version}"
        )
    data = path.read_bytes()
    return ServedWheel(
        path=path,
        filename=path.name,
        version=wheel_version,
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
    )


def _served(request: Request) -> ServedWheel:
    wheel: ServedWheel | None = request.app.state.served_wheel
    if wheel is None:
        raise HTTPException(status_code=404, detail="wheel not found")
    return wheel


@router.get("")
async def cli_info(request: Request) -> dict:
    wheel = _served(request)
    return {
        "version": wheel.version,
        "filename": wheel.filename,
        "sha256": wheel.sha256,
        "size": wheel.size,
    }


@router.get("/wheel")
async def cli_wheel(request: Request) -> FileResponse:
    wheel = _served(request)
    return FileResponse(
        wheel.path,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{wheel.filename}"'},
    )


def _simple_page(links: list[tuple[str, str]]) -> HTMLResponse:
    anchors = "\n".join(f'<a href="{href}">{html.escape(text)}</a><br/>' for href, text in links)
    return HTMLResponse(
        f"<!DOCTYPE html>\n<html><body>\n{anchors}\n</body></html>\n",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/simple/")
async def simple_index(request: Request) -> HTMLResponse:
    _served(request)
    return _simple_page([("evalkit/", "evalkit")])


@router.get("/simple/evalkit/")
async def simple_project(request: Request) -> HTMLResponse:
    wheel = _served(request)
    href = f"../../files/{quote(wheel.filename)}#sha256={wheel.sha256}"
    return _simple_page([(href, wheel.filename)])


@router.get("/files/{filename}")
async def cli_file(filename: str, request: Request) -> FileResponse:
    wheel = _served(request)
    if filename != wheel.filename:
        raise HTTPException(status_code=404, detail="wheel not found")
    return FileResponse(wheel.path, media_type="application/octet-stream")
