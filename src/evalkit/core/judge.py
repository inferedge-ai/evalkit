import httpx
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
    usage = JudgeUsage()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    async with httpx.AsyncClient(
        base_url=cfg.base_url,
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=300,
    ) as client:
        for attempt in range(2):
            response = await client.post(
                "/chat/completions",
                json={
                    "model": model or cfg.model,
                    "messages": messages,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": output_type.__name__,
                            "schema": output_type.model_json_schema(),
                        },
                    },
                    "max_tokens": cfg.max_tokens,
                    "reasoning_effort": cfg.reasoning_effort,
                },
                headers=judge_headers(session_id, evaluator_name),
            )
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
