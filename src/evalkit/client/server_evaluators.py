from typing import Any

from evalkit.client.api import AsyncApi, encode
from evalkit.core.server_evaluators import (
    ExtensionEvaluatorInfo,
    ServerScore,
    server_evaluator_timeout,
)


class HttpServerEvaluators:
    def __init__(self, api: AsyncApi):
        self._api = api

    async def score(self, experiment_run_id: int, name: str, config: dict[str, Any]) -> ServerScore:
        resp = await self._api.post(
            f"/api/experiment-runs/{experiment_run_id}/server-evaluations/{encode(name)}",
            json={"config": config},
            timeout=server_evaluator_timeout(),
            retry=False,
        )
        return ServerScore.model_validate(resp.json())

    async def extension_evaluators(self) -> dict[str, ExtensionEvaluatorInfo]:
        rows = (await self._api.get("/api/extension-evaluators")).json()
        return {row["name"]: ExtensionEvaluatorInfo.model_validate(row) for row in rows}
