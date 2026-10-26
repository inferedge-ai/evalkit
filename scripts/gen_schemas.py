import json
import sys
from pathlib import Path

from pydantic.json_schema import models_json_schema

from evalkit.contracts.atif import ACCEPTED_SCHEMA_VERSIONS
from evalkit.contracts.custom_evaluator import EvalRequest, EvalResponse
from evalkit.contracts.esf import MappingDocument
from evalkit.contracts.harness import (
    Capabilities,
    FileList,
    RunCreated,
    RunList,
    RunListParams,
    RunRequest,
    RunStatus,
)

REF_TEMPLATE = "#/components/schemas/{model}"

API_KEY = {"apiKeyAuth": {"type": "apiKey", "in": "header", "name": "x-api-key"}}


def _components(models: list[type]) -> tuple[dict, dict[type, dict]]:
    refs, defs = models_json_schema(
        [(model, "validation") for model in models], ref_template=REF_TEMPLATE
    )
    schemas = defs.get("$defs", {})
    ref_for = {model: refs[(model, "validation")] for model in models}
    return schemas, ref_for


def _json_response(description: str, ref: dict) -> dict:
    return {"description": description, "content": {"application/json": {"schema": ref}}}


def _param_schema(prop: dict) -> dict:
    variants = prop.pop("anyOf", None)
    if variants:
        non_null = [v for v in variants if v.get("type") != "null"]
        if len(non_null) == 1:
            prop = {**prop, **non_null[0]}
    if prop.get("default") is None:
        prop.pop("default", None)
    prop.pop("title", None)
    return prop


def _query_params(model: type) -> list[dict]:
    schema = model.model_json_schema()  # type: ignore[attr-defined]
    required = set(schema.get("required", []))
    return [
        {
            "name": name,
            "in": "query",
            "required": name in required,
            "schema": _param_schema(prop),
        }
        for name, prop in schema["properties"].items()
    ]


def harness_openapi() -> dict:
    schemas, ref = _components([Capabilities, RunRequest, RunCreated, RunStatus, RunList, FileList])
    run_id = {
        "name": "run_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string"},
    }
    list_params = _query_params(RunListParams)
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "evalkit Harness API",
            "version": "v1",
            "description": (
                "The HTTP contract a client's agent harness implements so evalkit "
                "can drive it. evalkit is the caller; the harness is the server. "
                "Trajectories are ATIF documents — accepted schema_version values "
                "ship in atif-accepted.json."
            ),
        },
        "security": [{"apiKeyAuth": []}],
        "paths": {
            "/capabilities": {
                "get": {
                    "summary": "Self-declared capabilities, snapshotted per experiment",
                    "responses": {"200": _json_response("Capabilities", ref[Capabilities])},
                }
            },
            "/runs": {
                "post": {
                    "summary": "Start one agent run in a fresh session",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": ref[RunRequest]},
                            "multipart/form-data": {
                                "schema": {
                                    "type": "object",
                                    "description": (
                                        "Used when the case has fixture files and the harness "
                                        "accepts `files` but not `file_urls`: a `payload` JSON "
                                        "part plus one part per file. When `accepts.file_urls`, "
                                        "the JSON body carries `files: [{name, url}]` instead "
                                        "and no bytes are uploaded."
                                    ),
                                    "properties": {
                                        "payload": ref[RunRequest],
                                        "files": {
                                            "type": "array",
                                            "items": {
                                                "type": "string",
                                                "format": "binary",
                                            },
                                        },
                                    },
                                    "required": ["payload"],
                                }
                            },
                        },
                    },
                    "responses": {"201": _json_response("Run created", ref[RunCreated])},
                },
                "get": {
                    "summary": "List runs/sessions (capability: run_listing)",
                    "parameters": list_params,
                    "responses": {"200": _json_response("Run listing", ref[RunList])},
                },
            },
            "/runs/{run_id}": {
                "get": {
                    "summary": "Poll run status until terminal",
                    "parameters": [run_id],
                    "responses": {"200": _json_response("Run status", ref[RunStatus])},
                }
            },
            "/runs/{run_id}/stop": {
                "post": {
                    "summary": "Cancel a run (capability: accepts.stop; called on timeout)",
                    "parameters": [run_id],
                    "responses": {"200": {"description": "Stop acknowledged"}},
                }
            },
            "/runs/{run_id}/trajectory": {
                "get": {
                    "summary": "The run's trajectory as an ATIF document",
                    "description": (
                        "Only meaningful after a terminal status. evalkit fetches it "
                        "once, validates it against the ATIF required subset, and "
                        "persists it. Schema: ATIF (Harbor framework); accepted "
                        "versions in atif-accepted.json."
                    ),
                    "parameters": [run_id],
                    "responses": {
                        "200": {
                            "description": "ATIF trajectory document",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                }
            },
            "/runs/{run_id}/files": {
                "get": {
                    "summary": "Workspace listing (capability: workspace)",
                    "parameters": [run_id],
                    "responses": {"200": _json_response("Workspace files", ref[FileList])},
                }
            },
            "/runs/{run_id}/files/{path}": {
                "get": {
                    "summary": "Workspace file bytes (capability: workspace)",
                    "parameters": [
                        run_id,
                        {
                            "name": "path",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "description": "File bytes",
                            "content": {
                                "application/octet-stream": {
                                    "schema": {"type": "string", "format": "binary"}
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {"schemas": schemas, "securitySchemes": API_KEY},
    }


def custom_evaluator_openapi() -> dict:
    schemas, ref = _components([EvalRequest, EvalResponse])
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "evalkit Custom Evaluator API",
            "version": "v1",
            "description": (
                "The HTTP contract a client implements to expose whitebox "
                "evaluators and gauges. One endpoint per named evaluator, "
                "registered in config.yaml; the path below is that configured "
                "endpoint. Synchronous; errors and timeouts are recorded as "
                "errored scores, never fatal. score=null means 'no reading'."
            ),
        },
        "security": [{"apiKeyAuth": []}],
        "paths": {
            "/{configured-endpoint}": {
                "post": {
                    "summary": "Score one run for one named evaluator",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": ref[EvalRequest]}},
                    },
                    "responses": {"200": _json_response("Named scores", ref[EvalResponse])},
                }
            }
        },
        "components": {"schemas": schemas, "securitySchemes": API_KEY},
    }


def generate(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    atif = {"accepted_schema_versions": sorted(ACCEPTED_SCHEMA_VERSIONS)}
    (out_dir / "harness-api-v1.json").write_text(json.dumps(harness_openapi(), indent=2) + "\n")
    (out_dir / "custom-evaluator-api-v1.json").write_text(
        json.dumps(custom_evaluator_openapi(), indent=2) + "\n"
    )
    (out_dir / "atif-accepted.json").write_text(json.dumps(atif, indent=2) + "\n")
    (out_dir / "span-mapping-v1.json").write_text(
        json.dumps(MappingDocument.model_json_schema(by_alias=True), indent=2) + "\n"
    )


if __name__ == "__main__":
    generate(Path(sys.argv[1]) if len(sys.argv) > 1 else Path("contracts"))
