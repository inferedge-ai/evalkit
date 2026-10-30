from fastapi import APIRouter

from evalkit import version

router = APIRouter(prefix="/api/cli", tags=["cli"])


@router.get("")
async def cli_info() -> dict:
    return {"version": version.current(), "package": version.DISTRIBUTION}
