import re
from dataclasses import dataclass

import httpx2
from pydantic import BaseModel, ValidationError

from evalkit.contracts.settings import JudgeSettings


class JudgeUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


def judge_headers(session_id: str | None, evaluator_name: str) -> dict[str, str]:
    headers = {"x-litellm-tags": f"eval-judge,evaluator:{evaluator_name}"}
    if session_id:
        headers["x-litellm-session-id"] = session_id
    return headers


_MAX_PARAMETER_ADAPTATIONS = 3

_UNSUPPORTED_CODES = ("unsupported_parameter", "unknown_parameter")

_MAX_TOKENS_PHRASES = (
    "max_tokens is not supported",
    "max_tokens not supported",
    "unsupported parameter: 'max_tokens'",
)

_REASONING_EFFORT_PHRASES = (
    "unrecognized request argument",
    "unknown parameter",
    "unsupported parameter: 'reasoning_effort'",
    "unsupported parameter: reasoning_effort",
    "reasoning_effort is not supported",
    "reasoning_effort not supported",
)

_TOKEN_LIMIT = re.compile(
    r"max_(?:completion_)?tokens is too large: \d+\. "
    r"this model supports at most (\d+) completion tokens"
)


@dataclass
class _Adaptations:
    max_completion_tokens: bool = False
    drop_reasoning_effort: bool = False
    token_limit: int | None = None

    def parameters(self, cfg: JudgeSettings) -> dict[str, object]:
        tokens = cfg.max_tokens
        if self.token_limit is not None:
            tokens = min(tokens, self.token_limit)
        key = "max_completion_tokens" if self.max_completion_tokens else "max_tokens"
        params: dict[str, object] = {key: tokens}
        if cfg.reasoning_effort and not self.drop_reasoning_effort:
            params["reasoning_effort"] = cfg.reasoning_effort
        return params


_ADAPTATIONS: dict[tuple[str, str], _Adaptations] = {}


def _error_details(response: httpx2.Response) -> tuple[str, str | None, str | None]:
    try:
        body = response.json()
    except ValueError:
        body = {}
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, str):
        return error.lower(), None, None
    if not isinstance(error, dict):
        return response.text.lower(), None, None
    message = str(error.get("message") or response.text).lower()
    param = error.get("param")
    code = error.get("code")
    return (
        message,
        str(param) if param is not None else None,
        str(code).lower() if code is not None else None,
    )


def _learn(adaptations: _Adaptations, sent: dict[str, object], response: httpx2.Response) -> bool:
    if response.status_code != 400:
        return False
    message, param, code = _error_details(response)
    rejected = code in _UNSUPPORTED_CODES
    if "max_tokens" in sent and (
        (rejected and param == "max_tokens")
        or (
            "max_completion_tokens" in message
            and any(phrase in message for phrase in _MAX_TOKENS_PHRASES)
        )
    ):
        adaptations.max_completion_tokens = True
        return True
    if "reasoning_effort" in sent and (
        (rejected and param == "reasoning_effort")
        or (
            "reasoning_effort" in message
            and any(phrase in message for phrase in _REASONING_EFFORT_PHRASES)
        )
    ):
        adaptations.drop_reasoning_effort = True
        return True
    limit = _TOKEN_LIMIT.search(message)
    sent_tokens = sent.get("max_tokens", sent.get("max_completion_tokens"))
    if limit and isinstance(sent_tokens, int) and int(limit.group(1)) < sent_tokens:
        found = int(limit.group(1))
        known = adaptations.token_limit
        adaptations.token_limit = found if known is None else min(known, found)
        return True
    return False


async def call_judge[T: BaseModel](
    cfg: JudgeSettings,
    *,
    api_key: str | None,
    system_prompt: str,
    user_prompt: str,
    output_type: type[T],
    session_id: str | None = None,
    evaluator_name: str,
    model: str | None = None,
) -> tuple[T, JudgeUsage]:
    if cfg.base_url is None:
        raise ValueError(f"judge provider {cfg.provider} is called by the evalkit server")
    usage = JudgeUsage()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    async with httpx2.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=300,
    ) as client:
        adaptations = _ADAPTATIONS.setdefault((cfg.base_url, model or cfg.model), _Adaptations())
        payload: dict[str, object] = {
            "model": model or cfg.model,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": output_type.__name__,
                    "schema": output_type.model_json_schema(),
                },
            },
        }
        for attempt in range(2):
            for _ in range(_MAX_PARAMETER_ADAPTATIONS + 1):
                params = adaptations.parameters(cfg)
                response = await client.post(
                    "/chat/completions",
                    json={**payload, "messages": messages, **params},
                    headers=judge_headers(session_id, evaluator_name),
                )
                if not _learn(adaptations, params, response):
                    break
            response.raise_for_status()
            body = response.json()
            call_usage = body.get("usage") or {}
            usage.input_tokens += call_usage.get("prompt_tokens") or 0
            usage.output_tokens += call_usage.get("completion_tokens") or 0
            content = body["choices"][0]["message"]["content"] or ""
            try:
                return output_type.model_validate_json(content), usage
            except ValidationError as exc:
                if attempt == 1:
                    raise
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {
                        "role": "user",
                        "content": "Your previous response was not valid JSON for the "
                        f"required schema: {exc.errors()[0]['msg']}. "
                        "Respond again with only the corrected JSON object.",
                    },
                ]
    raise AssertionError("unreachable")
