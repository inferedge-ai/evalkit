from typing import Annotated

from fastapi import APIRouter
from pydantic import AfterValidator, BaseModel

from evalkit.api.access import ActorDep, writable, writable_endpoint
from evalkit.api.deps import AgentStoreDep, ConfigDep, KeysDep, SessionDep
from evalkit.contracts.enums import TrajectorySource
from evalkit.contracts.settings import AgentSettings
from evalkit.core.agents import AgentDefinition, driver
from evalkit.core.workspace import Reference, slug

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _http_url(value: str) -> str:
    if not value.lower().startswith(("http://", "https://")):
        raise ValueError("repo_url must start with http:// or https://")
    return value


HttpUrlStr = Annotated[str, AfterValidator(_http_url)]


def agent_view(agent: AgentDefinition) -> dict:
    return {
        "id": agent.id,
        "name": agent.name,
        "workspace": agent.workspace,
        "ref": agent.ref,
        "slug": slug(Reference(agent.name, agent.workspace)),
        "workspace_type": "shared" if agent.workspace_is_shared else "personal",
        "harness_url": agent.harness_url,
        "trajectory_source": agent.trajectory_source,
        "repo_url": agent.repo_url,
        "settings": agent.settings.model_dump(),
        "driver": driver(agent),
        "archived": agent.archived,
        "has_api_key": agent.has_api_key,
    }


@router.get("")
async def list_agents(
    agents: AgentStoreDep, workspace: str | None = None, include_archived: bool = False
) -> list[dict]:
    return [
        agent_view(a)
        for a in await agents.list_agents(workspace=workspace, include_archived=include_archived)
    ]


@router.get("/{ref}")
async def get_agent(ref: str, agents: AgentStoreDep) -> dict:
    return agent_view(await agents.get(ref))


class CreateAgent(BaseModel):
    workspace: str
    name: str
    harness_url: str | None = None
    trajectory_source: TrajectorySource = TrajectorySource.ATIF
    repo_url: HttpUrlStr | None = None
    settings: AgentSettings = AgentSettings()
    mapping: dict | None = None
    api_key: str | None = None


@router.post("")
async def create_agent(
    body: CreateAgent,
    keys: KeysDep,
    agents: AgentStoreDep,
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
        url=body.harness_url,
    )
    created = await agents.create(
        body.workspace,
        body.name,
        harness_url=body.harness_url,
        trajectory_source=body.trajectory_source,
        repo_url=body.repo_url,
        settings=body.settings,
        created_by=actor.id,
        mapping=body.mapping,
        api_key_encrypted=keys.seal(body.api_key),
    )
    return agent_view(created)


class EditAgent(BaseModel):
    harness_url: str | None = None
    repo_url: HttpUrlStr | None = None
    settings: AgentSettings | None = None
    trajectory_source: TrajectorySource | None = None
    mapping: dict | None = None
    api_key: str | None = None


@router.patch("/{ref}")
async def edit_agent(
    ref: str,
    body: EditAgent,
    keys: KeysDep,
    agents: AgentStoreDep,
    session: SessionDep,
    actor: ActorDep,
    cfg: ConfigDep,
) -> dict:
    current = await agents.get(ref)
    url_changes = "harness_url" in body.model_fields_set and body.harness_url != current.harness_url
    await writable_endpoint(
        session,
        actor,
        cfg,
        current.workspace,
        api_key="api_key" in body.model_fields_set,
        url=body.harness_url if url_changes else None,
    )
    clear = {k for k in ("harness_url", "repo_url", "api_key") if k in body.model_fields_set}
    edited = await agents.edit(
        ref,
        harness_url=body.harness_url,
        repo_url=body.repo_url,
        settings=body.settings,
        clear=frozenset(clear),
        trajectory_source=body.trajectory_source,
        mapping=body.mapping,
        edited_by=actor.id,
        api_key_encrypted=keys.seal(body.api_key),
    )
    return agent_view(edited)


@router.post("/{ref}/archive")
async def archive_agent(
    ref: str, agents: AgentStoreDep, session: SessionDep, actor: ActorDep
) -> dict:
    current = await agents.get(ref)
    await writable(session, actor, current.workspace)
    return agent_view(await agents.archive(ref))


@router.post("/{ref}/restore")
async def restore_agent(
    ref: str, agents: AgentStoreDep, session: SessionDep, actor: ActorDep
) -> dict:
    current = await agents.get(ref)
    await writable(session, actor, current.workspace)
    return agent_view(await agents.restore(ref))
