from fastapi import APIRouter

from evalkit.api.access import StoresDep
from evalkit.api.deps import ConfigDep

router = APIRouter(prefix="/api/meta", tags=["meta"])
appearance_router = APIRouter(prefix="/api/appearance", tags=["meta"])


@router.get("")
async def meta(cfg: ConfigDep, stores: StoresDep) -> dict:
    every = await stores.agents.list_agents()
    return {
        "agents": [a.ref for a in every if a.workspace_is_shared and a.harness_url],
        "agent_repos": {a.ref: a.repo_url for a in every if a.repo_url},
        "settle_seconds": cfg.otel.settle_seconds,
        "trace_sources": [source.name for source in await stores.sources.list_sources()],
    }


@appearance_router.get("")
async def get_appearance(cfg: ConfigDep) -> dict:
    return {"accent_color": cfg.server.accent_color}
