from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from evalkit.api.access import ActorDep, ensure_active, require, workspace_ref
from evalkit.api.auth import CurrentUser
from evalkit.api.deps import AgentStoreDep, AgentTokensDep, SessionDep
from evalkit.core.access import can_write
from evalkit.core.agents import explicit
from evalkit.core.workspace import parse_reference

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenRequest(BaseModel):
    agent: str


@router.post("")
async def create_token(
    body: TokenRequest,
    agents: AgentStoreDep,
    tokens: AgentTokensDep,
    session: SessionDep,
    actor: ActorDep,
    user: CurrentUser,
) -> dict:
    try:
        ref = parse_reference(body.agent, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = await agents.get(explicit(ref))
    ws = await workspace_ref(session, record.workspace)
    require(can_write(actor, ws), f"you cannot mint tokens for {record.ref}")
    ensure_active(ws)
    if record.archived:
        raise HTTPException(status_code=409, detail=f"agent {record.ref} is archived")
    token, info = await tokens.create(agent_id=record.id, user_id=user.id)
    return {"id": info.id, "token": token, "agent": info.agent}


@router.get("")
async def list_tokens(tokens: AgentTokensDep, user: CurrentUser) -> list[dict]:
    rows = await tokens.list(user_id=None if user.is_admin else user.id)
    return [r.model_dump(mode="json") for r in rows]


@router.delete("/{token_id}")
async def revoke_token(token_id: int, tokens: AgentTokensDep, user: CurrentUser) -> dict:
    if not await tokens.revoke(token_id, user_id=None if user.is_admin else user.id):
        raise HTTPException(status_code=404, detail="unknown token")
    return {"ok": True}
