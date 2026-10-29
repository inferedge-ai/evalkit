from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from evalkit.api.access import ActorDep, ensure_active, require, workspace_ref
from evalkit.api.agents import PERSONAL_KEY
from evalkit.api.deps import ConfigDep, SessionDep
from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.settings import RemoteEvaluatorSettings
from evalkit.core.access import can_write
from evalkit.core.endpoints import check_endpoint
from evalkit.core.remote_evaluators import RemoteEvaluator
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.keys import KeyLoader

router = APIRouter(prefix="/api/evaluators", tags=["evaluators"])


def _store(request: Request) -> RemoteEvaluatorStore:
    return RemoteEvaluatorStore(request.app.state.session_factory)


def _view(evaluator: RemoteEvaluator) -> dict:
    return {**evaluator.model_dump(mode="json"), "ref": evaluator.ref}


def _keys(request: Request) -> KeyLoader:
    return request.app.state.keys


async def _writable(
    session: SessionDep, actor: ActorDep, workspace: str, *, api_key: bool = False
) -> None:
    ws = await workspace_ref(session, workspace)
    require(can_write(actor, ws), f"you cannot write to @{workspace}")
    ensure_active(ws)
    if api_key and not ws.is_shared:
        raise HTTPException(status_code=400, detail=PERSONAL_KEY)


@router.get("")
async def list_evaluators(
    request: Request, workspace: str | None = None, include_archived: bool = False
) -> list[dict]:
    rows = await _store(request).list_evaluators(
        workspace=workspace, include_archived=include_archived
    )
    return [_view(e) for e in rows]


class CreateEvaluator(BaseModel):
    workspace: str
    name: str
    kind: EvaluatorKind
    settings: RemoteEvaluatorSettings
    api_key: str | None = None


@router.post("")
async def create_evaluator(
    body: CreateEvaluator, request: Request, session: SessionDep, actor: ActorDep, cfg: ConfigDep
) -> dict:
    await _writable(session, actor, body.workspace, api_key="api_key" in body.model_fields_set)
    await check_endpoint(body.settings.endpoint, allow_private=cfg.allow_private_endpoints)
    created = await _store(request).create(
        body.workspace,
        body.name,
        body.kind,
        body.settings,
        _keys(request).seal(body.api_key) if body.api_key else None,
    )
    return _view(created)


class EditEvaluator(BaseModel):
    settings: RemoteEvaluatorSettings | None = None
    api_key: str | None = None


@router.patch("/{ref}")
async def edit_evaluator(
    ref: str,
    body: EditEvaluator,
    request: Request,
    session: SessionDep,
    actor: ActorDep,
    cfg: ConfigDep,
) -> dict:
    current = await _store(request).get(ref)
    await _writable(session, actor, current.workspace, api_key="api_key" in body.model_fields_set)
    if body.settings is not None and body.settings.endpoint != current.settings.endpoint:
        await check_endpoint(body.settings.endpoint, allow_private=cfg.allow_private_endpoints)
    edited = await _store(request).edit(
        ref,
        body.settings,
        api_key_encrypted=_keys(request).seal(body.api_key) if body.api_key else None,
        clear_api_key="api_key" in body.model_fields_set and body.api_key is None,
    )
    return _view(edited)


@router.get("/{ref}")
async def get_evaluator(ref: str, request: Request) -> dict:
    return _view(await _store(request).get(ref))


@router.post("/{ref}/archive")
async def archive_evaluator(
    ref: str, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    current = await _store(request).get(ref)
    await _writable(session, actor, current.workspace)
    return _view(await _store(request).archive(ref))


@router.post("/{ref}/restore")
async def restore_evaluator(
    ref: str, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    current = await _store(request).get(ref)
    await _writable(session, actor, current.workspace)
    return _view(await _store(request).restore(ref))
