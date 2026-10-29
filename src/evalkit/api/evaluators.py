from fastapi import APIRouter, Request
from pydantic import BaseModel

from evalkit.api.access import ActorDep
from evalkit.api.agents import sealed, writable, writable_endpoint
from evalkit.api.deps import ConfigDep, KeysDep, SessionDep
from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.settings import RemoteEvaluatorSettings
from evalkit.core.remote_evaluators import RemoteEvaluator
from evalkit.db.evaluators import RemoteEvaluatorStore

router = APIRouter(prefix="/api/evaluators", tags=["evaluators"])


def _store(request: Request) -> RemoteEvaluatorStore:
    return RemoteEvaluatorStore(request.app.state.session_factory)


def _view(evaluator: RemoteEvaluator) -> dict:
    return {**evaluator.model_dump(mode="json"), "ref": evaluator.ref}


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
    body: CreateEvaluator,
    request: Request,
    keys: KeysDep,
    session: SessionDep,
    actor: ActorDep,
    cfg: ConfigDep,
) -> dict:
    await writable_endpoint(
        session,
        actor,
        cfg,
        body.workspace,
        api_key="api_key" in body.model_fields_set,
        url=body.settings.endpoint,
    )
    created = await _store(request).create(
        body.workspace,
        body.name,
        body.kind,
        body.settings,
        sealed(keys, body.api_key),
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
    keys: KeysDep,
    session: SessionDep,
    actor: ActorDep,
    cfg: ConfigDep,
) -> dict:
    current = await _store(request).get(ref)
    moved = body.settings is not None and body.settings.endpoint != current.settings.endpoint
    await writable_endpoint(
        session,
        actor,
        cfg,
        current.workspace,
        api_key="api_key" in body.model_fields_set,
        url=body.settings.endpoint if moved and body.settings is not None else None,
    )
    edited = await _store(request).edit(
        ref,
        body.settings,
        api_key_encrypted=sealed(keys, body.api_key),
        clear_api_key="api_key" in body.model_fields_set and not body.api_key,
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
    await writable(session, actor, current.workspace)
    return _view(await _store(request).archive(ref))


@router.post("/{ref}/restore")
async def restore_evaluator(
    ref: str, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    current = await _store(request).get(ref)
    await writable(session, actor, current.workspace)
    return _view(await _store(request).restore(ref))
