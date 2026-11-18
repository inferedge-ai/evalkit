from fastapi import APIRouter

from evalkit.api.access import StoresDep
from evalkit.core.evaluators.base import BUILTIN_EVALUATORS
from evalkit.core.server_evaluators import ExtensionEvaluatorInfo

router = APIRouter(tags=["evaluators"])


@router.get("/api/builtin-evaluators")
async def list_builtin_evaluators() -> list[dict]:
    return [
        {"name": e.name, "uses_judge": e.uses_judge, "local_only": e.local_only}
        for e in sorted(BUILTIN_EVALUATORS.values(), key=lambda e: e.name)
    ]


@router.get("/api/extension-evaluators")
async def list_extension_evaluators(stores: StoresDep) -> list[dict]:
    return [
        ExtensionEvaluatorInfo(
            name=name, uses_judge=e.uses_judge, requires_trajectory=e.requires_trajectory
        ).model_dump()
        for name, e in sorted(stores.deps.extension_evaluators.items())
    ]
