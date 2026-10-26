from fastapi import APIRouter, Request
from pydantic import BaseModel

from evalkit.api.access import ActorDep, ensure_active, require, workspace_ref
from evalkit.api.deps import SessionDep
from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.settings import RemoteEvaluatorSettings
from evalkit.core.access import can_set_endpoint, can_write
from evalkit.core.remote_evaluators import RemoteEvaluator
from evalkit.db.evaluators import RemoteEvaluatorStore

router = APIRouter(prefix="/api/evaluators", tags=["evaluators"])


def _store(request: Request) -> RemoteEvaluatorStore:
    return RemoteEvaluatorStore(request.app.state.session_factory)


def _view(evaluator: RemoteEvaluator) -> dict:
    return {**evaluator.model_dump(mode="json"), "ref": evaluator.ref}


async def _writable(
    session: SessionDep, actor: ActorDep, workspace: str, *, endpoint: bool = False
) -> None:
    ws = await workspace_ref(session, workspace)
    require(can_write(actor, ws), f"you cannot write to @{workspace}")
    require(
        not endpoint or can_set_endpoint(actor, ws),
        f"only an admin may set an evaluator's endpoint in the shared workspace @{workspace}",
    )
    ensure_active(ws)


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


@router.post("")
async def create_evaluator(
    body: CreateEvaluator, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    await _writable(session, actor, body.workspace, endpoint=True)
    created = await _store(request).create(body.workspace, body.name, body.kind, body.settings)
    return _view(created)


class EditEvaluator(BaseModel):
    settings: RemoteEvaluatorSettings


@router.patch("/{ref}")
async def edit_evaluator(
    ref: str, body: EditEvaluator, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    current = await _store(request).get(ref)
    await _writable(
        session,
        actor,
        current.workspace,
        endpoint=body.settings.endpoint != current.settings.endpoint,
    )
    return _view(await _store(request).edit(ref, body.settings))


@router.post("/{ref}/archive")
async def archive_evaluator(
    ref: str, request: Request, session: SessionDep, actor: ActorDep
) -> dict:
    current = await _store(request).get(ref)
    await _writable(session, actor, current.workspace)
    return _view(await _store(request).archive(ref))
