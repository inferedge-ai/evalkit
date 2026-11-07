from fastapi import APIRouter

from evalkit.core.evaluators.base import BUILTIN_EVALUATORS

router = APIRouter(tags=["evaluators"])


@router.get("/api/builtin-evaluators")
async def list_builtin_evaluators() -> list[dict]:
    return [
        {"name": e.name, "uses_judge": e.uses_judge, "local_only": e.local_only}
        for e in sorted(BUILTIN_EVALUATORS.values(), key=lambda e: e.name)
    ]
