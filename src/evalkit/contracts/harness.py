import json
from datetime import datetime
from typing import Literal

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field, model_validator


class HarnessModel(BaseModel):
    model_config = ConfigDict(extra="allow")


DEFAULT_INPUT_SCHEMA: dict = {
    "type": "object",
    "required": ["question"],
    "properties": {"question": {"type": "string"}},
}


def input_errors(schema: dict, run_input: dict) -> list[str]:
    validator_cls = validator_for(schema)
    validator = validator_cls(schema)
    errors = sorted(validator.iter_errors(run_input), key=lambda e: list(e.absolute_path))
    return [
        f"{'/'.join(str(part) for part in e.absolute_path) or '<root>'}: {e.message}"
        for e in errors
    ]


def answer_text(answer: str | None, output: dict | None) -> str:
    if answer is not None:
        return answer
    return json.dumps(output, sort_keys=True, ensure_ascii=False)


class Provides(HarnessModel):
    trajectory: bool = Field(
        default=False, description="Can serve GET runs/{id}/trajectory at all."
    )
    tool_calls: bool = Field(default=False, description="Trajectory reliably contains tool calls.")
    thinking: bool = Field(
        default=False, description="Trajectory reliably contains reasoning content."
    )
    token_usage: bool = Field(default=False, description="Trajectory carries per-step token usage.")
    artifacts: bool = Field(
        default=False, description="Run status reports artifacts the agent produced."
    )
    workspace: bool = Field(default=False, description="Files endpoints are implemented.")
    run_listing: bool = Field(
        default=False,
        description="GET /runs (list) is implemented. Powers the sessions browser.",
    )


class Accepts(HarnessModel):
    files: bool = Field(default=False, description="POST /runs accepts multipart file attachments.")
    file_urls: bool = Field(
        default=False,
        description="POST /runs (JSON body) accepts `files: [{name, url}]` and the harness "
        "fetches each url itself. Preferred over multipart when both are declared. The url "
        "is evalkit's blob store url plus the object key (e.g. s3://bucket/fixtures/<key>); "
        "the harness must have access to it.",
    )
    model_override: bool = Field(default=False, description="POST /runs honours the 'model' field.")
    stop: bool = Field(default=False, description="POST /runs/{id}/stop is implemented.")

    @property
    def file_input(self) -> bool:
        return self.file_urls or self.files


class Limits(HarnessModel):
    max_concurrent_runs: int | None = Field(
        default=None,
        description="Harness-side cap. evalkit uses min(this, its own config).",
    )


class Capabilities(HarnessModel):
    contract_version: str = Field(
        default="v1", description="Harness API version this harness implements."
    )
    agent: str = Field(description="Harness's own name for itself. Informational.")
    revision: str | None = Field(
        default=None,
        description="Optional. Git commit SHA of the deployed harness; evalkit "
        "records it on the experiment so results are traceable to a build.",
    )
    branch: str | None = Field(
        default=None, description="Optional. Git branch the deployed harness was built from."
    )
    input_schema: dict | None = Field(
        default=None,
        description="Optional. JSON Schema (draft 2020-12 unless `$schema` says otherwise) "
        "of the run input this agent accepts. Excludes `files`, which evalkit sends "
        "separately. When absent, evalkit assumes the default question-and-answer schema: "
        "an object with a required string `question`. evalkit checks case inputs against "
        "the declared or default schema before running an experiment and in "
        "`evalkit lint --agent`.",
    )
    check_input: dict | None = Field(
        default=None,
        description="Optional. The run input `evalkit harness check` sends. Must pass "
        "input_schema, or the default question-and-answer schema when none is declared.",
    )
    provides: Provides = Field(default_factory=Provides)
    accepts: Accepts = Field(default_factory=Accepts)
    limits: Limits = Field(default_factory=Limits)

    @property
    def effective_input_schema(self) -> dict:
        return self.input_schema or DEFAULT_INPUT_SCHEMA

    @model_validator(mode="after")
    def _schema_and_check_input_agree(self) -> "Capabilities":
        if self.input_schema is not None:
            try:
                validator_for(self.input_schema).check_schema(self.input_schema)
            except SchemaError as exc:
                raise ValueError(f"input_schema is not a valid JSON Schema: {exc.message}") from exc
        if self.check_input is not None:
            errors = input_errors(self.effective_input_schema, self.check_input)
            if errors:
                raise ValueError(f"check_input fails input_schema: {errors[0]}")
        return self


class RunMetadata(HarnessModel):
    experiment_id: str | None = Field(
        default=None,
        description="Experiment id, so eval traffic is distinguishable and traceable.",
    )
    case_id: str | None = Field(default=None, description="Case id for the harness's records.")
    repetition: int | None = Field(default=None, description="Repetition number, 1-based.")


class FileRef(HarnessModel):
    name: str = Field(description="File name to present to the agent — the fixture key.")
    url: str | None = Field(
        default=None,
        description="Where the harness fetches the bytes from. Present on a JSON request "
        "(accepts.file_urls); absent on multipart, where the bytes arrive as the part with "
        "this name. Opaque to evalkit's experiment runner; the UI still serves the same "
        "fixture via GET /api/fixtures/{name}.",
    )
    context: str | None = Field(
        default=None,
        description="What the file holds, from the suite's fixture registry. evalkit no "
        "longer prepends it to input.question; show it to the agent however suits it.",
    )


class RunRequest(HarnessModel):
    input: dict = Field(
        description="The case input, verbatim; evalkit never rewrites it. Free-form "
        "object (see capabilities.input_schema); the harness maps it "
        "onto its own invocation."
    )
    model: str | None = Field(
        default=None, description="Optional. Only sent if accepts.model_override."
    )
    metadata: RunMetadata | None = Field(
        default=None,
        description="Optional, for the harness's own records. The harness may ignore it.",
    )
    files: list[FileRef] | None = Field(
        default=None,
        description="Optional. The case's fixture files, each with its context. On a JSON "
        "request (accepts.file_urls) each carries the url to fetch; on multipart it is in "
        "the payload part without urls, next to one file part per entry.",
    )


class RunCreated(HarnessModel):
    run_id: str = Field(
        description="The harness's own identifier — typically its session id. "
        "Key for every other endpoint."
    )


class ArtifactRef(HarnessModel):
    name: str = Field(description="Artifact display name.")
    path: str | None = Field(
        default=None, description="Workspace path, fetchable via the files endpoint."
    )
    content_type: str | None = Field(default=None, description="MIME type, if known.")


class RunStatus(HarnessModel):
    status: Literal["queued", "running", "completed", "failed", "cancelled"] = Field(
        description="Run state. evalkit polls until terminal or timeout."
    )
    answer: str | None = Field(
        default=None,
        description="Final user-visible answer. A completed run carries this, `output`, or both.",
    )
    output: dict | None = Field(
        default=None,
        description="Optional. The agent's structured result, for agents that return "
        "typed data. A completed run carries this, `answer`, or both.",
    )
    model: str = Field(
        description="Required. The model this run executes on — the requested "
        "model when honoured, otherwise the harness's own default. evalkit "
        "records it as the experiment's model and prefers it over the request.",
    )
    artifacts: list[ArtifactRef] | None = Field(
        default=None, description="Optional. Files the agent produced as deliverables."
    )
    error: str | None = Field(
        default=None,
        description="Message when status=failed. A completed run may also carry a "
        "non-fatal error (e.g. partial answer).",
    )

    @model_validator(mode="after")
    def _completed_carries_answer_or_output(self) -> "RunStatus":
        if self.status == "completed" and self.answer is None and self.output is None:
            raise ValueError("a completed run must carry an answer or an output")
        return self


class RunListParams(BaseModel):
    query: str | None = Field(
        default=None,
        description="Free-text filter over runs. Text that looks like an id (8 or more "
        "hex characters; dashes and case ignored) matches runs whose id starts with it. "
        "Any other text matches runs whose title contains it, case-insensitive.",
    )
    user_id: str | None = Field(default=None, description="Filter to one user's runs.")
    org_id: str | None = Field(default=None, description="Filter to one org's runs.")
    limit: int = Field(default=50, description="Page size.")
    offset: int = Field(default=0, description="Page start.")
    since: datetime | None = Field(
        default=None, description="Only runs created at or after this time (ISO 8601)."
    )
    until: datetime | None = Field(
        default=None, description="Only runs created before this time (ISO 8601)."
    )

    def as_query(self) -> dict[str, str | int]:
        return {
            k: v.isoformat().replace("+00:00", "Z") if isinstance(v, datetime) else v
            for k, v in self.model_dump().items()
            if v is not None
        }


class RunListItem(HarnessModel):
    run_id: str = Field(description="Usable with the other /runs/{id} endpoints.")
    title: str | None = Field(default=None, description="Display title for the run/session.")
    user_id: str | None = Field(
        default=None, description="Optional filter value; fixed dummy if no multi-tenancy."
    )
    org_id: str | None = Field(
        default=None, description="Optional filter value; fixed dummy if no multi-tenancy."
    )
    created_at: datetime | None = Field(default=None, description="Run creation time.")
    session_id: str | None = Field(
        default=None,
        description="The session this run belongs to, for a harness that keeps several of its "
        "own runs per session (for example one per chat message) and answers each of them with "
        "the whole session. evalkit's run is the session: items sharing a session_id are listed "
        "once, as the newest of them.",
    )


class RunList(HarnessModel):
    runs: list[RunListItem] = Field(description="Page of runs, newest first.")
    total: int = Field(description="Total runs matching the query.")


class FileEntry(HarnessModel):
    path: str = Field(description="Workspace-relative path.")
    size: int = Field(description="Size in bytes.")
    content_type: str | None = Field(default=None, description="MIME type, if known.")


class FileList(HarnessModel):
    files: list[FileEntry] = Field(description="Workspace listing.")
