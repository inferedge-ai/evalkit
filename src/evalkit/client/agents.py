from evalkit.client.api import AsyncApi, encode
from evalkit.contracts.settings import AgentSettings
from evalkit.core.agents import AgentDefinition


def definition_of(body: dict) -> AgentDefinition:
    return AgentDefinition(
        id=body["id"],
        name=body["name"],
        workspace=body["workspace"],
        workspace_is_shared=body["workspace_type"] == "shared",
        harness_url=body["harness_url"],
        trajectory_source=body["trajectory_source"],
        repo_url=body["repo_url"],
        settings=AgentSettings.model_validate(body["settings"]),
        archived=body["archived"],
    )


class HttpAgents:
    def __init__(self, api: AsyncApi):
        self._api = api

    async def get(self, ref: str) -> AgentDefinition:
        return definition_of((await self._api.get(f"/api/agents/{encode(ref)}")).json())
