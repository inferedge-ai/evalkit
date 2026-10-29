from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from evalkit.api.auth import AdminUser
from evalkit.config import Config
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.endpoints import check_endpoint
from evalkit.db.keys import KeyLoader
from evalkit.db.llm_providers import LlmProviderStore

router = APIRouter(tags=["llm providers"])

NO_DEFAULT_JUDGE = "no default judge; an admin sets one with evalkit judge-default set"


def _store(request: Request) -> LlmProviderStore:
    return request.app.state.llm_providers


def _keys(request: Request) -> KeyLoader:
    return request.app.state.keys


def _cfg(request: Request) -> Config:
    return request.app.state.cfg


@router.get("/api/llm-providers")
async def list_llm_providers(request: Request, include_archived: bool = False) -> list[dict]:
    rows = await _store(request).list_providers(include_archived=include_archived)
    return [row.model_dump(mode="json") for row in rows]


@router.get("/api/llm-providers/{name}")
async def get_llm_provider(name: str, request: Request) -> dict:
    return (await _store(request).get(name)).model_dump(mode="json")


class CreateLlmProvider(BaseModel):
    name: str
    base_url: str
    api_key: str | None = None


@router.post("/api/llm-providers")
async def create_llm_provider(body: CreateLlmProvider, request: Request, admin: AdminUser) -> dict:
    await check_endpoint(body.base_url, allow_private=_cfg(request).allow_private_endpoints)
    sealed = _keys(request).seal(body.api_key) if body.api_key else None
    created = await _store(request).create(body.name, body.base_url, sealed)
    return created.model_dump(mode="json")


class EditLlmProvider(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


@router.patch("/api/llm-providers/{name}")
async def edit_llm_provider(
    name: str, body: EditLlmProvider, request: Request, admin: AdminUser
) -> dict:
    current = await _store(request).get(name)
    if body.base_url is not None and body.base_url != current.base_url:
        await check_endpoint(body.base_url, allow_private=_cfg(request).allow_private_endpoints)
    edited = await _store(request).edit(
        name,
        base_url=body.base_url,
        api_key_encrypted=_keys(request).seal(body.api_key) if body.api_key else None,
        clear_api_key="api_key" in body.model_fields_set and body.api_key is None,
    )
    return edited.model_dump(mode="json")


@router.post("/api/llm-providers/{name}/archive")
async def archive_llm_provider(name: str, request: Request, admin: AdminUser) -> dict:
    return (await _store(request).archive(name)).model_dump(mode="json")


@router.post("/api/llm-providers/{name}/restore")
async def restore_llm_provider(name: str, request: Request, admin: AdminUser) -> dict:
    return (await _store(request).restore(name)).model_dump(mode="json")


@router.get("/api/judge-default")
async def get_judge_default(request: Request) -> dict:
    judge = await _store(request).default_judge()
    if judge is None:
        raise HTTPException(status_code=404, detail=NO_DEFAULT_JUDGE)
    return judge.model_dump()


@router.put("/api/judge-default")
async def set_judge_default(body: JudgeSettings, request: Request, admin: AdminUser) -> dict:
    return (await _store(request).set_default_judge(body)).model_dump()
