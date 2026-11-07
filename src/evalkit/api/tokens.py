from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from evalkit.api.access import ActorDep, StoresDep, ensure_active, require, workspace_ref
from evalkit.api.auth import CurrentUser
from evalkit.core.access import can_write
from evalkit.core.agents import explicit
from evalkit.core.workspace import parse_reference

router = APIRouter(prefix="/api/tokens", tags=["tokens"])


class TokenRequest(BaseModel):
    agent: str


@router.post("")
async def create_token(
    body: TokenRequest,
    stores: StoresDep,
    actor: ActorDep,
    user: CurrentUser,
) -> dict:
    try:
        ref = parse_reference(body.agent, user.username)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    record = await stores.agents.get(explicit(ref))
    ws = await workspace_ref(stores, record.workspace)
    require(can_write(actor, ws), f"you cannot mint tokens for {record.ref}")
    ensure_active(ws)
    if record.archived:
        raise HTTPException(status_code=409, detail=f"agent {record.ref} is archived")
    token, info = await stores.tokens.create(agent_id=record.id, user_id=user.id)
    return {"id": info.id, "token": token, "agent": info.agent}


@router.get("")
async def list_tokens(stores: StoresDep, user: CurrentUser) -> list[dict]:
    rows = await stores.tokens.list(user_id=None if user.is_admin else user.id)
    return [r.model_dump(mode="json") for r in rows]


@router.delete("/{token_id}")
async def revoke_token(token_id: int, stores: StoresDep, user: CurrentUser) -> dict:
    if not await stores.tokens.revoke(token_id, user_id=None if user.is_admin else user.id):
        raise HTTPException(status_code=404, detail="unknown token")
    return {"ok": True}
