from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from evalkit.api.auth import AdminUser
from evalkit.api.deps import ConfigDep, KeysDep
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.endpoints import check_endpoint
from evalkit.db.llm_providers import LlmProviderStore

router = APIRouter(tags=["llm providers"])

NO_DEFAULT_JUDGE = "no default judge; an admin sets one with evalkit judge-default set"


def _store(request: Request) -> LlmProviderStore:
    return request.app.state.llm_providers


async def require_registered_judge(providers: LlmProviderStore, base_url: str) -> None:
    if await providers.active_by_base_url(base_url) is None:
        registered = ", ".join(p.base_url for p in await providers.list_providers())
        raise HTTPException(
            status_code=400,
            detail=f"{base_url} is not a registered LLM provider; "
            f"registered: {registered or 'none'}",
        )


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
async def create_llm_provider(
    body: CreateLlmProvider, request: Request, keys: KeysDep, cfg: ConfigDep, admin: AdminUser
) -> dict:
    await check_endpoint(body.base_url, allow_private=cfg.allow_private_endpoints)
    created = await _store(request).create(body.name, body.base_url, keys.seal(body.api_key))
    return created.model_dump(mode="json")


class EditLlmProvider(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


@router.patch("/api/llm-providers/{name}")
async def edit_llm_provider(
    name: str,
    body: EditLlmProvider,
    request: Request,
    keys: KeysDep,
    cfg: ConfigDep,
    admin: AdminUser,
) -> dict:
    current = await _store(request).get(name)
    if body.base_url is not None and body.base_url != current.base_url:
        await check_endpoint(body.base_url, allow_private=cfg.allow_private_endpoints)
    edited = await _store(request).edit(
        name,
        base_url=body.base_url,
        api_key_encrypted=keys.seal(body.api_key),
        clear_api_key="api_key" in body.model_fields_set and not body.api_key,
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


class JudgePatch(BaseModel):
    base_url: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None
    concurrency: int | None = None


@router.patch("/api/judge-default")
async def patch_judge_default(body: JudgePatch, request: Request, admin: AdminUser) -> dict:
    current = await _store(request).default_judge()
    merged = {
        **(current.model_dump() if current is not None else {}),
        **body.model_dump(exclude_none=True),
    }
    judge = JudgeSettings.model_validate(merged)
    return (await _store(request).set_default_judge(judge)).model_dump()
