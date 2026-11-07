from fastapi import APIRouter
from pydantic import BaseModel

from evalkit.api.access import ActorDep, StoresDep, writable, writable_endpoint
from evalkit.api.deps import ConfigDep
from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.settings import RemoteEvaluatorSettings
from evalkit.core.remote_evaluators import RemoteEvaluator

router = APIRouter(prefix="/api/evaluators", tags=["evaluators"])


def _view(evaluator: RemoteEvaluator) -> dict:
    return {**evaluator.model_dump(mode="json"), "ref": evaluator.ref}


@router.get("")
async def list_evaluators(
    stores: StoresDep, workspace: str | None = None, include_archived: bool = False
) -> list[dict]:
    rows = await stores.evaluators.list_evaluators(
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
    stores: StoresDep,
    actor: ActorDep,
    cfg: ConfigDep,
) -> dict:
    ws = await writable_endpoint(
        stores,
        actor,
        cfg,
        body.workspace,
        api_key="api_key" in body.model_fields_set,
        url=body.settings.endpoint,
    )
    created = await stores.evaluators.create(
        ws,
        body.name,
        body.kind,
        body.settings,
        stores.keys.seal(body.api_key),
    )
    return _view(created)


class EditEvaluator(BaseModel):
    settings: RemoteEvaluatorSettings | None = None
    api_key: str | None = None


@router.patch("/{ref}")
async def edit_evaluator(
    ref: str,
    body: EditEvaluator,
    stores: StoresDep,
    actor: ActorDep,
    cfg: ConfigDep,
) -> dict:
    current = await stores.evaluators.get(ref)
    moved = body.settings is not None and body.settings.endpoint != current.settings.endpoint
    await writable_endpoint(
        stores,
        actor,
        cfg,
        current.workspace,
        api_key="api_key" in body.model_fields_set,
        url=body.settings.endpoint if moved and body.settings is not None else None,
    )
    edited = await stores.evaluators.edit(
        ref,
        body.settings,
        api_key_encrypted=stores.keys.seal(body.api_key),
        clear_api_key="api_key" in body.model_fields_set and not body.api_key,
    )
    return _view(edited)


@router.get("/{ref}")
async def get_evaluator(ref: str, stores: StoresDep) -> dict:
    return _view(await stores.evaluators.get(ref))


@router.post("/{ref}/archive")
async def archive_evaluator(ref: str, stores: StoresDep, actor: ActorDep) -> dict:
    current = await stores.evaluators.get(ref)
    await writable(stores, actor, current.workspace)
    return _view(await stores.evaluators.archive(ref))


@router.post("/{ref}/restore")
async def restore_evaluator(ref: str, stores: StoresDep, actor: ActorDep) -> dict:
    current = await stores.evaluators.get(ref)
    await writable(stores, actor, current.workspace)
    return _view(await stores.evaluators.restore(ref))
