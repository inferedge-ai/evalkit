from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from evalkit.api.access import StoresDep
from evalkit.api.auth import AdminUser
from evalkit.api.deps import ConfigDep
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.endpoints import check_endpoint
from evalkit.db.llm_providers import LlmProviderStore

router = APIRouter(tags=["llm providers"])

NO_DEFAULT_JUDGE = "no default judge; an admin sets one with evalkit judge-default set"


async def require_provider_judge(providers: LlmProviderStore, judge: JudgeSettings) -> str:
    registered = ", ".join(p.name for p in await providers.list_providers()) or "none"
    if judge.provider is None:
        raise HTTPException(
            status_code=400,
            detail=f"the server judges with a registered LLM provider, not a base_url "
            f"({judge.base_url}); pass a provider: {registered}",
        )
    if await providers.active_endpoint(judge.provider) is None:
        raise HTTPException(
            status_code=400,
            detail=f"{judge.provider} is not a registered LLM provider; registered: {registered}",
        )
    return judge.provider


@router.get("/api/llm-providers")
async def list_llm_providers(stores: StoresDep, include_archived: bool = False) -> list[dict]:
    rows = await stores.providers.list_providers(include_archived=include_archived)
    return [row.model_dump(mode="json") for row in rows]


@router.get("/api/llm-providers/{name}")
async def get_llm_provider(name: str, stores: StoresDep) -> dict:
    return (await stores.providers.get(name)).model_dump(mode="json")


class CreateLlmProvider(BaseModel):
    name: str
    base_url: str
    api_key: str | None = None


@router.post("/api/llm-providers")
async def create_llm_provider(
    body: CreateLlmProvider, stores: StoresDep, cfg: ConfigDep, admin: AdminUser
) -> dict:
    await check_endpoint(body.base_url, allow_private=cfg.allow_private_endpoints)
    created = await stores.providers.create(
        body.name, body.base_url, stores.keys.seal(body.api_key)
    )
    return created.model_dump(mode="json")


class EditLlmProvider(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


@router.patch("/api/llm-providers/{name}")
async def edit_llm_provider(
    name: str,
    body: EditLlmProvider,
    stores: StoresDep,
    cfg: ConfigDep,
    admin: AdminUser,
) -> dict:
    current = await stores.providers.get(name)
    if body.base_url is not None and body.base_url != current.base_url:
        await check_endpoint(body.base_url, allow_private=cfg.allow_private_endpoints)
    edited = await stores.providers.edit(
        name,
        base_url=body.base_url,
        api_key_encrypted=stores.keys.seal(body.api_key),
        clear_api_key="api_key" in body.model_fields_set and not body.api_key,
    )
    return edited.model_dump(mode="json")


@router.post("/api/llm-providers/{name}/archive")
async def archive_llm_provider(name: str, stores: StoresDep, admin: AdminUser) -> dict:
    return (await stores.providers.archive(name)).model_dump(mode="json")


@router.post("/api/llm-providers/{name}/restore")
async def restore_llm_provider(name: str, stores: StoresDep, admin: AdminUser) -> dict:
    return (await stores.providers.restore(name)).model_dump(mode="json")


@router.get("/api/judge-default")
async def get_judge_default(stores: StoresDep) -> dict:
    judge = await stores.providers.default_judge()
    if judge is None:
        raise HTTPException(status_code=404, detail=NO_DEFAULT_JUDGE)
    return judge.model_dump(exclude_none=True)


@router.put("/api/judge-default")
async def set_judge_default(body: JudgeSettings, stores: StoresDep, admin: AdminUser) -> dict:
    return (await stores.providers.set_default_judge(body)).model_dump(exclude_none=True)


class JudgePatch(BaseModel):
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    max_tokens: int | None = None


@router.patch("/api/judge-default")
async def patch_judge_default(body: JudgePatch, stores: StoresDep, admin: AdminUser) -> dict:
    current = await stores.providers.default_judge()
    merged = {
        **(current.model_dump(exclude_none=True) if current is not None else {}),
        **body.model_dump(exclude_none=True),
    }
    judge = JudgeSettings.model_validate(merged)
    return (await stores.providers.set_default_judge(judge)).model_dump(exclude_none=True)
