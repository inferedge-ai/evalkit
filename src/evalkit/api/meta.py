from fastapi import APIRouter, Request

from evalkit.api.deps import AgentStoreDep, ConfigDep

router = APIRouter(prefix="/api/meta", tags=["meta"])
judge_router = APIRouter(tags=["meta"])


@judge_router.get("/api/judge-default")
async def judge_default(cfg: ConfigDep) -> dict:
    return cfg.judge_default.model_dump()


@router.get("")
async def meta(request: Request, cfg: ConfigDep, agents: AgentStoreDep) -> dict:
    every = await agents.list_agents()
    return {
        "accent_color": cfg.server.accent_color,
        "agents": [a.ref for a in every if a.workspace_is_shared and a.harness_url],
        "agent_repos": {a.ref: a.repo_url for a in every if a.repo_url},
        "settle_seconds": cfg.otel.settle_seconds,
        "trace_sources": [
            source.name for source in await request.app.state.trace_sources.list_sources()
        ],
    }
