import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

RUN_CREATED_AT = datetime(2026, 9, 1, tzinfo=UTC)
DEFAULT_TRAJECTORY = json.loads((Path(__file__).parent / "data" / "atif_full.json").read_text())


class FakeHarness:
    def __init__(
        self,
        *,
        capabilities_overrides: dict | None = None,
        answer: str | None = "The answer is 42.",
        trajectory_doc: dict | None = None,
        complete_after_polls: int = 0,
        fail_run: bool = False,
        api_key: str = "client-key",
        workspace_files: dict[str, bytes] | None = None,
        model: str = "fake/model-1",
        reject_multipart: str | None = None,
        output: dict | None = None,
        stop_settles_after_polls: int = 0,
        ignore_time_filter: bool = False,
        page_cap: int | None = None,
    ):
        self.ignore_time_filter = ignore_time_filter
        self.page_cap = page_cap
        self.answer = answer
        self.stop_settles_after_polls = stop_settles_after_polls
        self.output = output
        self.reject_multipart = reject_multipart
        self.model = model
        self.trajectory_doc = trajectory_doc if trajectory_doc is not None else DEFAULT_TRAJECTORY
        self.complete_after_polls = complete_after_polls
        self.fail_run = fail_run
        self.api_key = api_key
        self.workspace_files = workspace_files or {}
        self.runs: dict[str, dict] = {}
        self.stop_calls: list[str] = []
        self.received_files: dict[str, list[tuple[str, bytes]]] = {}
        self.received_file_urls: dict[str, list[dict]] = {}
        self.received_payloads: dict[str, dict] = {}
        self._counter = 0
        self.capabilities = {
            "contract_version": "v1",
            "agent": "fake",
            "provides": {
                "trajectory": True,
                "tool_calls": True,
                "thinking": True,
                "token_usage": True,
                "artifacts": True,
                "workspace": True,
                "run_listing": True,
            },
            "accepts": {
                "files": True,
                "file_urls": False,
                "model_override": True,
                "stop": True,
            },
            "limits": {"max_concurrent_runs": 8},
        }
        for key, value in (capabilities_overrides or {}).items():
            if isinstance(value, dict):
                self.capabilities[key] = {**self.capabilities.get(key, {}), **value}
            else:
                self.capabilities[key] = value
        self.app = self._build_app()

    def _build_app(self) -> FastAPI:
        app = FastAPI()

        @app.middleware("http")
        async def check_auth(request: Request, call_next):
            if request.headers.get("x-api-key") != self.api_key:
                from starlette.responses import JSONResponse

                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            return await call_next(request)

        @app.get("/evalkit/v1/capabilities")
        async def capabilities():
            return self.capabilities

        @app.post("/evalkit/v1/runs", status_code=201)
        async def start_run(request: Request):
            self._counter += 1
            run_id = f"sess-{self._counter}"
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("multipart/"):
                if self.reject_multipart is not None:
                    raise HTTPException(status_code=400, detail=self.reject_multipart)
                form = await request.form()
                payload = json.loads(form["payload"])
                files = []
                for key, value in form.multi_items():
                    if key != "payload":
                        files.append((value.filename, await value.read()))
                self.received_files[run_id] = files
            else:
                payload = await request.json()
                if payload.get("files"):
                    self.received_file_urls[run_id] = payload["files"]
            self.received_payloads[run_id] = payload
            self.runs[run_id] = {
                "polls": 0,
                "stopped": False,
                "payload": payload,
                "model": payload.get("model") or self.model,
            }
            return {"run_id": run_id}

        @app.get("/evalkit/v1/runs")
        async def list_runs(
            query: str | None = None,
            user_id: str | None = None,
            org_id: str | None = None,
            limit: int = 50,
            offset: int = 0,
            since: datetime | None = None,
            until: datetime | None = None,
        ):
            items = [
                {
                    "run_id": run_id,
                    "title": f"run {run_id}",
                    "user_id": "dummy",
                    "org_id": run.get("org_id", "dummy"),
                    "created_at": run.get("created_at", RUN_CREATED_AT).isoformat(),
                    "session_id": run.get("session_id"),
                }
                for run_id, run in self.runs.items()
                if org_id is None or run.get("org_id", "dummy") == org_id
                if self.ignore_time_filter
                or (
                    (since is None or run.get("created_at", RUN_CREATED_AT) >= since)
                    and (until is None or run.get("created_at", RUN_CREATED_AT) < until)
                )
            ]
            size = min(limit, self.page_cap or limit)
            return {"runs": items[offset : offset + size], "total": len(items)}

        @app.get("/evalkit/v1/runs/{run_id}")
        async def run_status(run_id: str):
            run = self._get(run_id)
            run["polls"] += 1
            model = run.get("model") or self.model
            if run["stopped"]:
                run["polls_after_stop"] += 1
                if run["polls_after_stop"] <= self.stop_settles_after_polls:
                    return {"status": "running", "model": model}
                return {"status": "cancelled", "model": model}
            if self.fail_run:
                return {"status": "failed", "error": "agent crashed", "model": model}
            if run["polls"] > self.complete_after_polls:
                completed = {
                    "status": "completed",
                    "answer": self.answer,
                    "model": model,
                    "artifacts": [
                        {"name": "report.md", "path": "outputs/report.md"},
                    ],
                }
                if self.output is not None:
                    completed["output"] = self.output
                return completed
            return {"status": "running", "model": model}

        @app.post("/evalkit/v1/runs/{run_id}/stop")
        async def stop_run(run_id: str):
            run = self._get(run_id)
            run["stopped"] = True
            run["polls_after_stop"] = 0
            self.stop_calls.append(run_id)
            return {"status": "cancelled"}

        @app.get("/evalkit/v1/runs/{run_id}/trajectory")
        async def trajectory(run_id: str):
            self._get(run_id)
            return self.trajectory_doc

        @app.get("/evalkit/v1/runs/{run_id}/files")
        async def list_files(run_id: str):
            self._get(run_id)
            return {
                "files": [
                    {"path": path, "size": len(data), "content_type": None}
                    for path, data in self.workspace_files.items()
                ]
            }

        @app.get("/evalkit/v1/runs/{run_id}/files/{path:path}")
        async def get_file(run_id: str, path: str):
            self._get(run_id)
            if path not in self.workspace_files:
                raise HTTPException(status_code=404)
            from starlette.responses import Response

            return Response(content=self.workspace_files[path])

        return app

    def _get(self, run_id: str) -> dict:
        if run_id not in self.runs:
            raise HTTPException(status_code=404, detail="unknown run")
        return self.runs[run_id]
