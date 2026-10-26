from evalkit.client.api import AsyncApi
from evalkit.core.remote_evaluators import RemoteEvaluator


class HttpEvaluators:
    def __init__(self, api: AsyncApi):
        self._api = api

    async def in_workspace(self, workspace: str) -> dict[str, RemoteEvaluator]:
        rows = (await self._api.get("/api/evaluators", params={"workspace": workspace})).json()
        return {row["name"]: RemoteEvaluator.model_validate(row) for row in rows}
