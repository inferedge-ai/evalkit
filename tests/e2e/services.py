"""Host-side fakes for a manual end-to-end run: the fake harness, the fake custom
evaluator and a judge that answers any OpenAI-compatible structured request with
a schema-valid object. Not collected by pytest."""

from __future__ import annotations

import re
import sys
import threading

import uvicorn
from fastapi import FastAPI, Request

from tests.core.test_runner import custom_eval_app
from tests.fake_harness import FakeHarness


def _fill(schema: dict, defs: dict, count: int, index: int = 1) -> object:
    if "$ref" in schema:
        return _fill(defs[schema["$ref"].split("/")[-1]], defs, count, index)
    if "anyOf" in schema:
        options = [o for o in schema["anyOf"] if o.get("type") != "null"]
        return (
            None
            if not options or "null" in [o.get("type") for o in schema["anyOf"]]
            else _fill(options[0], defs, count, index)
        )
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {
            name: (index if name == "index" else _fill(prop, defs, count, index))
            for name, prop in (schema.get("properties") or {}).items()
        }
    if kind == "array":
        return [_fill(schema["items"], defs, count, i + 1) for i in range(count)]
    if kind == "string":
        return "e2e fake judge"
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    return None


def judge_app() -> FastAPI:
    app = FastAPI()

    @app.post("/chat/completions")
    async def completions(request: Request) -> dict:
        body = await request.json()
        schema = body["response_format"]["json_schema"]["schema"]
        user = next(m["content"] for m in body["messages"] if m["role"] == "user")
        numbered = len(re.findall(r"^\s*\d+\.\s", user, re.M)) or 1
        content = _fill(schema, schema.get("$defs") or {}, numbered)
        import json

        return {
            "choices": [{"message": {"role": "assistant", "content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            "model": body.get("model"),
        }

    return app


def serve(app, port: int) -> None:
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    harness = FakeHarness(
        answer="The FY2025 net debt is 42 million; free cash flow rose 10%.",
        workspace_files={"outputs/report.md": b"# e2e report\n"},
        api_key="client-key",
    )
    threads = [
        threading.Thread(target=serve, args=(harness.app, 18080), daemon=True),
        threading.Thread(target=serve, args=(custom_eval_app(), 19000), daemon=True),
        threading.Thread(target=serve, args=(judge_app(), 19090), daemon=True),
    ]
    for t in threads:
        t.start()
    print("e2e services up: harness :18080, custom evaluator :19000, judge :19090", flush=True)
    threading.Event().wait()
    sys.exit(0)
