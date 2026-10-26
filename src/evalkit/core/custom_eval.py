import logging

import httpx
from pydantic import BaseModel, ValidationError

from evalkit.contracts.custom_evaluator import (
    EvalCase,
    EvalRequest,
    EvalResponse,
    EvalRun,
    ScoreItem,
)
from evalkit.core.remote_evaluators import RemoteEvaluator

LOGGER = logging.getLogger(__name__)


class EvalError(BaseModel):
    evaluator: str
    message: str


async def score_with_custom_evaluator(
    evaluator: RemoteEvaluator,
    api_key: str | None,
    *,
    case: EvalCase,
    answer: str,
    trajectory: dict,
    output: dict | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> list[ScoreItem] | EvalError:
    request = EvalRequest(
        evaluator=evaluator.name,
        kind=evaluator.wire_kind,
        case=case,
        run=EvalRun(answer=answer, output=output, trajectory=trajectory),
    )
    try:
        async with httpx.AsyncClient(
            headers={"x-api-key": api_key} if api_key else {},
            timeout=evaluator.settings.timeout_seconds,
            transport=transport,
        ) as client:
            resp = await client.post(evaluator.settings.endpoint, json=request.model_dump())
            resp.raise_for_status()
            return EvalResponse.model_validate(resp.json()).scores
    except (httpx.HTTPError, ValidationError, ValueError) as exc:
        LOGGER.warning("custom evaluator %s errored: %s", evaluator.ref, exc)
        return EvalError(evaluator=evaluator.name, message=str(exc))
