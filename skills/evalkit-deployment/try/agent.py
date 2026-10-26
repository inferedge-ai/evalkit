import json
import os
from typing import Any

import httpx
from opentelemetry import trace

NAME = "analyst"
MAX_TURNS = 6
FACTS = {
    "revenue_fy2025": "48.2 million",
    "revenue_fy2024": "41.5 million",
    "gross_profit_fy2025": "18.5 million",
    "debt_fy2025": "20.0 million",
    "cash_fy2025": "12.7 million",
}
INSTRUCTIONS = (
    "You are a financial analyst answering questions about Acme Widgets. "
    "Look up every figure with lookup_financials; never guess one. "
    "State figures in millions, and percentages to one decimal place."
)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_financials",
            "description": "One reported figure for Acme Widgets.",
            "parameters": {
                "type": "object",
                "properties": {"metric": {"type": "string", "enum": sorted(FACTS)}},
                "required": ["metric"],
            },
        },
    }
]

tracer = trace.get_tracer("acme-analyst")


def lookup_financials(metric: str) -> str:
    return FACTS.get(metric, f"unknown metric {metric}")


def gen_ai_message(message: dict[str, Any]) -> dict[str, Any]:
    if message["role"] == "tool":
        parts = [
            {
                "type": "tool_call_response",
                "id": message["tool_call_id"],
                "response": message["content"],
            }
        ]
    else:
        parts = [{"type": "text", "content": message["content"]}] if message.get("content") else []
        parts += [
            {
                "type": "tool_call",
                "id": call["id"],
                "name": call["function"]["name"],
                "arguments": json.loads(call["function"]["arguments"] or "{}"),
            }
            for call in message.get("tool_calls") or []
        ]
    return {"role": message["role"], "parts": parts}


class Analyst:
    def __init__(self, client: httpx.Client | None = None):
        self.model = os.environ["LLM_MODEL"]
        self.client = client or httpx.Client(
            base_url=os.environ["LLM_BASE_URL"],
            headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"},
            timeout=120,
        )

    def chat(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        with tracer.start_as_current_span(f"chat {self.model}") as span:
            span.set_attribute("gen_ai.operation.name", "chat")
            span.set_attribute("gen_ai.request.model", self.model)
            span.set_attribute(
                "gen_ai.system_instructions",
                json.dumps([{"type": "text", "content": INSTRUCTIONS}]),
            )
            span.set_attribute(
                "gen_ai.input.messages", json.dumps([gen_ai_message(m) for m in messages])
            )
            response = self.client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "system", "content": INSTRUCTIONS}, *messages],
                    "tools": TOOLS,
                },
            )
            response.raise_for_status()
            body = response.json()
            reply = body["choices"][0]["message"]
            span.set_attribute("gen_ai.output.messages", json.dumps([gen_ai_message(reply)]))
            usage = body.get("usage") or {}
            span.set_attribute("gen_ai.usage.input_tokens", usage.get("prompt_tokens") or 0)
            span.set_attribute("gen_ai.usage.output_tokens", usage.get("completion_tokens") or 0)
            return reply

    def call_tool(self, call: dict[str, Any]) -> str:
        name = call["function"]["name"]
        with tracer.start_as_current_span(f"execute_tool {name}") as span:
            span.set_attribute("gen_ai.operation.name", "execute_tool")
            span.set_attribute("gen_ai.tool.name", name)
            span.set_attribute("gen_ai.tool.call.id", call["id"])
            span.set_attribute("gen_ai.tool.call.arguments", call["function"]["arguments"])
            arguments = json.loads(call["function"]["arguments"] or "{}")
            result = lookup_financials(**arguments)
            span.set_attribute("gen_ai.tool.call.result", result)
            return result

    def ask(self, question: str) -> str:
        with tracer.start_as_current_span(f"invoke_agent {NAME}") as span:
            span.set_attribute("gen_ai.operation.name", "invoke_agent")
            span.set_attribute("gen_ai.agent.name", NAME)
            messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
            for _ in range(MAX_TURNS):
                reply = self.chat(messages)
                messages.append(
                    {k: v for k, v in reply.items() if k in ("role", "content", "tool_calls")}
                )
                if not reply.get("tool_calls"):
                    return reply.get("content") or ""
                for call in reply["tool_calls"]:
                    result = self.call_tool(call)
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
            raise RuntimeError(f"no answer after {MAX_TURNS} model calls")
