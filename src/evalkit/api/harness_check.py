import asyncio

from fastapi import APIRouter, HTTPException, Request

from evalkit.api.access import ActorDep, require, workspace_ref
from evalkit.api.deps import AgentStoreDep, RunnerDepsDep, SessionDep
from evalkit.core.access import can_use_agent
from evalkit.core.agents import explicit, server_may_drive
from evalkit.core.harness_check import check_harness
from evalkit.core.workspace import parse_slug

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.post("/{agent}/check")
async def run_check(
    agent: str,
    request: Request,
    deps: RunnerDepsDep,
    agents: AgentStoreDep,
    session: SessionDep,
    actor: ActorDep,
) -> dict:
    definition = await agents.get(explicit(parse_slug(agent)))
    ws = await workspace_ref(session, definition.workspace)
    require(can_use_agent(actor, ws), f"only the owner of @{ws.name} can check {definition.ref}")
    if definition.archived:
        raise HTTPException(status_code=409, detail=f"agent {definition.ref} is archived")
    if definition.harness_url is None:
        raise HTTPException(status_code=400, detail=f"{definition.ref} has no harness_url")
    if not server_may_drive(definition):
        raise HTTPException(
            status_code=400,
            detail=f"the server does not drive {definition.ref}; its owner checks it with "
            f"evalkit harness check {definition.ref}",
        )
    report = await asyncio.wait_for(
        check_harness(
            definition,
            deps.credentials.key_for("agent", definition.ref),
            deps.blob_store,
            transport=deps.harness_transport,
            sessions=request.app.state.sessions,
        ),
        timeout=120,
    )
    return {"agent": agent, "checks": report}
