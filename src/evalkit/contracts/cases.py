"""Pydantic schema for eval case YAML files.

Validates shape of every case on import — before any blob write or store
call. Unknown evaluator keys or missing required fields fail loudly here so
the author fixes them locally instead of discovering bad config at run time.
"""

from __future__ import annotations

import hashlib
import json
import re
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal, Union, get_args, get_origin

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

AnswerRubricType = Literal[
    "factual_retrieval",
    "derived_computation",
    "analysis_interpretation",
]

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
REGISTRY_FILENAME = "fixtures.yaml"


class FixtureEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Annotated[str, Field(min_length=1)]
    sha256: str
    context: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def _flat_key_and_digest(self) -> FixtureEntry:
        if self.key in (".", "..") or "/" in self.key or "\\" in self.key:
            raise ValueError(f"fixture key {self.key!r} must be a flat filename")
        if not _SHA256_HEX.match(self.sha256):
            raise ValueError(f"fixture {self.key!r}: sha256 must be 64 lowercase hex chars")
        return self


class FixtureRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixtures: list[FixtureEntry] = Field(default_factory=list)

    @field_validator("fixtures", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any) -> Any:
        return [] if value is None else value


def load_registry(path: Path) -> FixtureRegistry:
    return FixtureRegistry.model_validate(yaml.safe_load(Path(path).read_text()) or {})


def is_case_yaml(path: Path) -> bool:
    path = Path(path)
    if path.suffix not in (".yaml", ".yml"):
        return False
    return path.name not in (REGISTRY_FILENAME, "manifest.yaml")


class Rubric(BaseModel):
    """One thing a correct answer contains, written as a sentence."""

    model_config = ConfigDict(extra="forbid")
    rubric: str = Field(
        min_length=1,
        description="One assertion a correct answer contains, as a single"
        " sentence. Carries its own subject, period, unit and expected value —"
        " a figure with no stated band accepts normal roundings of itself; state"
        " a band only to tighten or widen that. Never names a tool, skill or"
        " data provider, and never refers to another rubric.",
    )
    type: AnswerRubricType = Field(
        description="Which kind of check this is; drives the per-type rates."
    )
    source: str | None = Field(
        default=None,
        description="The source the answer must credit this fact to, named at the"
        " level of detail the case cares about. Graded on its own citation axis."
        " `factual_retrieval` only.",
    )

    @model_validator(mode="after")
    def _source_matches_type(self) -> Rubric:
        if self.source is not None and self.type != "factual_retrieval":
            raise ValueError(
                f"rubric {self.rubric!r} sets `source` on type {self.type!r}."
                " `source` is only valid on factual_retrieval: an"
                " analysis_interpretation assertion has no source by definition,"
                " and a derived_computation's sources are its inputs, plural"
            )
        return self


class AnswerRubrics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rubrics: list[Rubric] = Field(
        min_length=1,
        description="Everything the answer must contain, one sentence each.",
    )


class PresentationRubric(BaseModel):
    """One property of how the reply presents its information."""

    model_config = ConfigDict(extra="forbid")

    rubric: str | None = Field(
        default=None,
        description="A presentation property the answer must have. Write the"
        " property, not a description of the answer having it.",
    )
    forbid: str | None = Field(
        default=None,
        description="A presentation property the answer must NOT have. Costs its"
        " points when violated; it does not zero the case.",
    )
    points: int = Field(
        default=1,
        ge=1,
        le=10,
        description="Weight of this rubric, relative within the case.",
    )
    note: str | None = Field(
        default=None,
        description="Qualification of this rubric written by the case author, shown to the judge.",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> PresentationRubric:
        if (self.rubric is None) == (self.forbid is None):
            raise ValueError("a presentation rubric carries exactly one of rubric / forbid")
        return self


class PresentationJudge(BaseModel):
    """Grades HOW the reply presents its information — units shown, columns
    present, an artifact relayed rather than restated. Correctness of the
    figures belongs to answer_rubrics; asserting the same thing in both places
    reweights it rather than making the case stricter."""

    model_config = ConfigDict(extra="forbid")

    rubrics: list[PresentationRubric] = Field(
        min_length=1,
        description="The presentation properties this answer must satisfy. At least"
        " one must be a positive `rubric`.",
    )

    @model_validator(mode="after")
    def _not_only_guards(self) -> PresentationJudge:
        # An all-forbid rubric set scores 1.0 for an answer that violates nothing —
        # including one that says nothing at all — so it hands out free presentation
        # marks.
        if all(r.rubric is None for r in self.rubrics):
            raise ValueError(
                "presentation_judge carries only `forbid` rubrics, which scores 1.0"
                " for an answer that violates nothing. Add a positive `rubric` for"
                " what the answer must show, or drop presentation_judge from this case"
            )
        return self


class TrajectoryRubrics(BaseModel):
    """Checkpoints the recorded trajectory must show, one sentence each: an
    intermediate value the derivation needs ("Retrieves FY2024 Platform revenue
    as $3,522,776,000") or a process step in plain language ("Rounds the final
    figure to the nearest basis point"). Order is not graded. By convention a
    rubric never names a tool, skill or MCP server, so the case stays portable
    across harnesses."""

    model_config = ConfigDict(extra="forbid")
    rubrics: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)


class ExpectedToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, description="Tool name, exactly as the trajectory records it.")
    args: dict[str, Any] = Field(default_factory=dict)
    args_mode: Literal["subset", "exact", "ignore", "substring"] = Field(
        default="subset",
        description="subset: every expected key present and equal, extras allowed."
        " exact: args equal. ignore: name alone. substring: like subset, but each"
        " expected value need only appear, case-insensitively, inside the actual"
        " value (lists and dicts compared as JSON text).",
    )
    match_overrides: dict[str, Literal["equals", "substring"]] = Field(
        default_factory=dict,
        description="Per-arg mode, overriding the one args_mode implies. Keys must be in args.",
    )

    @model_validator(mode="after")
    def _validate_match_overrides(self) -> ExpectedToolCall:
        if not self.match_overrides:
            return self
        if self.args_mode in ("exact", "ignore"):
            raise ValueError("match_overrides can't be combined with args_mode 'exact' or 'ignore'")
        unknown = set(self.match_overrides) - set(self.args)
        if unknown:
            raise ValueError(f"match_overrides keys not present in args: {sorted(unknown)}")
        return self


class ToolTrajectoryMatch(BaseModel):
    """Expected tool calls, matched by name and args, each against at most one
    actual call. Scores the fraction found; `ordered` also requires them to
    appear in this order."""

    model_config = ConfigDict(extra="forbid")
    expected: list[ExpectedToolCall] = Field(min_length=1)
    ordered: bool = False


class RequiredToolsCalled(BaseModel):
    """Tools the agent must call at least once. Scores the fraction called."""

    model_config = ConfigDict(extra="forbid")
    tools: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)


class MaxResponseTime(BaseModel):
    """Wall-clock cap on the agent run."""

    model_config = ConfigDict(extra="forbid")
    max_seconds: float = Field(gt=0)


class OutputEvaluators(BaseModel):
    """Evaluators that look only at the agent's returned output."""

    model_config = ConfigDict(extra="forbid")

    answer_rubrics: AnswerRubrics | None = None
    presentation_judge: PresentationJudge | None = None
    max_response_time: MaxResponseTime | None = None


class TrajectoryEvaluators(BaseModel):
    """Evaluators that look only at how the agent got to its output."""

    model_config = ConfigDict(extra="forbid")

    trajectory_rubrics: TrajectoryRubrics | None = None
    tool_trajectory_match: ToolTrajectoryMatch | None = None
    required_tools_called: RequiredToolsCalled | None = None


class OverlayTrajectoryEvaluators(BaseModel):
    """The harness-specific trajectory evaluators an overlay may attach."""

    model_config = ConfigDict(extra="forbid")

    tool_trajectory_match: ToolTrajectoryMatch | None = None
    required_tools_called: RequiredToolsCalled | None = None

    def as_metadata(self) -> dict[str, Any]:
        return {
            name: _dump_evaluator_config(value)
            for name in type(self).model_fields
            if (value := getattr(self, name)) is not None
        }


def _dump_evaluator_config(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _dump_evaluator_config(v) for k, v in value.items()}
    return value


class CaseEvaluators(BaseModel):
    """Case's evaluator opt-ins: built-ins split into output and trajectory
    families, plus opaque custom-evaluator blocks keyed by registered name."""

    model_config = ConfigDict(extra="forbid")

    output: OutputEvaluators = Field(default_factory=OutputEvaluators)
    trajectory: TrajectoryEvaluators = Field(default_factory=TrajectoryEvaluators)
    custom: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def as_metadata(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for family in ("output", "trajectory"):
            sub = getattr(self, family)
            block = {
                name: _dump_evaluator_config(value)
                for name in type(sub).model_fields
                if (value := getattr(sub, name)) is not None
            }
            if block:
                out[family] = block
        if self.custom:
            out["custom"] = _dump_evaluator_config(self.custom)
        return out


class Case(BaseModel):
    """A single eval case authored in YAML."""

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1)]
    tags: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    fixtures: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
    expected: dict[str, Any] | None = None
    evaluators: CaseEvaluators = Field(default_factory=CaseEvaluators)


class OverlayEvaluators(BaseModel):
    model_config = ConfigDict(extra="forbid")

    custom: dict[str, dict[str, Any]] = Field(default_factory=dict)
    trajectory: OverlayTrajectoryEvaluators = Field(default_factory=OverlayTrajectoryEvaluators)

    @model_validator(mode="after")
    def _not_empty(self) -> OverlayEvaluators:
        if not self.custom and not self.trajectory.as_metadata():
            raise ValueError("an overlay must attach at least one custom or trajectory evaluator")
        return self


AgentName = Annotated[str, Field(min_length=1, pattern=r"^[^@]+$")]


class Overlay(BaseModel):
    """Extends a case in another suite by id without touching its ground truth: only
    additive tags, custom-evaluator config and tool trajectory evaluators are allowed."""

    model_config = ConfigDict(extra="forbid")

    extends: Annotated[str, Field(min_length=1)]
    agents: Annotated[list[AgentName], Field(min_length=1)]
    tags: list[str] = Field(default_factory=list)
    evaluators: OverlayEvaluators


class SuiteDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    max_response_time: Annotated[float, Field(gt=0)] | MaxResponseTime | None = None
    agents: list[AgentName] | None = None

    @field_validator("input", "tags", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None:
            return {} if info.field_name == "input" else []
        return value


class SuiteFile(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"patternProperties": {"^x-": {}}})

    defaults: SuiteDefaults = Field(default_factory=SuiteDefaults)
    cases: list[Case | Overlay] = Field(default_factory=list)
    overlays: list[Overlay] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _drop_anchor_keys(cls, data: Any) -> Any:
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if not str(k).startswith("x-")}
        return data

    @field_validator("defaults", "cases", "overlays", mode="before")
    @classmethod
    def _none_is_empty(cls, value: Any, info: ValidationInfo) -> Any:
        if value is None:
            return {} if info.field_name == "defaults" else []
        return value


CASE_FILE: TypeAdapter[SuiteFile | list[Case | Overlay] | Case] = TypeAdapter(
    SuiteFile | list[Case | Overlay] | Case
)


def _submodel(annotation: Any) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (list, Union, types.UnionType, Annotated):
        for arg in get_args(annotation):
            found = _submodel(arg)
            if found is not None:
                return found
    return None


def _model_at(model: type[BaseModel], loc: tuple[int | str, ...]) -> type[BaseModel] | None:
    current: type[BaseModel] | None = model
    for part in loc:
        if current is None:
            return None
        if isinstance(part, int) or part == current.__name__:
            continue
        field = current.model_fields.get(part)
        if field is None:
            return None
        current = _submodel(field.annotation)
    return current


def _evaluator_homes(model: type[BaseModel]) -> dict[str, str]:
    evaluators = _model_at(model, ("evaluators",))
    if evaluators is None:
        return {}
    return {
        name: f"evaluators.{family}"
        for family in evaluators.model_fields
        if (sub := _model_at(evaluators, (family,))) is not None
        for name in sub.model_fields
    }


EVALUATOR_HOMES = _evaluator_homes(Case)


def explain_schema_error(model: type[BaseModel], exc: ValidationError) -> list[str]:
    homes = _evaluator_homes(model)
    lines = []
    for error in exc.errors():
        loc = tuple(error["loc"])
        where = ".".join(str(part) for part in loc) or "(top level)"
        line = f"{where}: {error['msg']}"
        if error["type"] == "extra_forbidden" and loc:
            key = str(loc[-1])
            parent = _model_at(model, loc[:-1])
            if parent is not None:
                line += f"; valid keys here: {', '.join(parent.model_fields)}"
            if key in homes:
                line += f"; `{key}` goes under `{homes[key]}`"
            elif key in EVALUATOR_HOMES and model is Overlay:
                line += f"; `{key}` is not allowed in an overlay"
            elif key in EVALUATOR_HOMES:
                line += f"; `{key}` goes under `{EVALUATOR_HOMES[key]}` of a case"
            elif loc[:-1] == ("evaluators",) and homes:
                families = sorted(set(homes.values()))
                line += (
                    "; built-in evaluators sit under "
                    + " or ".join(f"`{family}`" for family in families)
                    + " (see case-schema.md)"
                )
        if error["type"] == "model_type":
            expected = _model_at(model, loc)
            if expected is not None:
                line += f"; expects a mapping with keys: {', '.join(expected.model_fields)}"
        lines.append(line)
    return lines


@dataclass
class SchemaProblem:
    case_id: str | None
    lines: list[str]


class CaseSchemaError(ValueError):
    def __init__(self, path: Path, problems: list[SchemaProblem]):
        self.path = path
        self.problems = problems
        super().__init__(
            f"{path}: "
            + "; ".join(
                f"{'case ' + repr(p.case_id) if p.case_id is not None else 'the file'}: "
                + " | ".join(p.lines)
                for p in problems
            )
        )


def _check(
    model: type[BaseModel], item: Any, id_key: str | None, problems: list[SchemaProblem]
) -> Any:
    case_id = item.get(id_key) if id_key is not None and isinstance(item, dict) else None
    try:
        return model.model_validate(item)
    except ValidationError as exc:
        problems.append(
            SchemaProblem(
                str(case_id) if case_id is not None else None, explain_schema_error(model, exc)
            )
        )
        return None


def _apply_defaults(case: Case, defaults: SuiteDefaults) -> Case:
    output = case.evaluators.output
    mrt = defaults.max_response_time
    if mrt is not None and output.max_response_time is None:
        output = output.model_copy(
            update={
                "max_response_time": mrt
                if isinstance(mrt, MaxResponseTime)
                else MaxResponseTime(max_seconds=mrt)
            }
        )
    return case.model_copy(
        update={
            "input": {**defaults.input, **case.input},
            "tags": list(dict.fromkeys(defaults.tags + case.tags)),
            "evaluators": case.evaluators.model_copy(update={"output": output}),
        }
    )


def _file_problem(path: Path, message: str) -> CaseSchemaError:
    return CaseSchemaError(path, [SchemaProblem(None, [message])])


def load_cases_from_yaml(path: Path) -> tuple[list[Case], list[Overlay]]:
    """Parse a YAML file into validated cases and overlays.

    Accepts these top-level shapes:
      - a single case dict
      - a list of case/overlay dicts (overlays identified by `extends`)
      - a mapping with any of `defaults`, `cases`, `overlays`, plus keys starting
        with `x-` that hold YAML anchors and are otherwise ignored
    """
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise _file_problem(path, f"not valid YAML: {exc}") from exc
    problems: list[SchemaProblem] = []
    defaults = SuiteDefaults()
    overlay_items: Any = []
    if isinstance(raw, dict) and ("cases" in raw or "overlays" in raw):
        shell = {k: v for k, v in raw.items() if k not in ("cases", "overlays")}
        suite = _check(SuiteFile, shell, None, problems)
        if suite is None:
            raise CaseSchemaError(path, problems)
        defaults = suite.defaults
        overlay_items = raw.get("overlays") or []
        if not isinstance(overlay_items, list):
            raise _file_problem(
                path, f"overlays: expected a list of overlays, got {type(overlay_items).__name__}"
            )
        raw = raw.get("cases") or []
        if not isinstance(raw, list):
            raise _file_problem(path, f"cases: expected a list of cases, got {type(raw).__name__}")
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise _file_problem(
            path,
            "expected a case, a list of cases and overlays, or a mapping with"
            f" defaults / cases / overlays; got {type(raw).__name__}",
        )
    case_items: list[Any] = []
    for item in raw:
        if isinstance(item, dict) and "extends" in item:
            overlay_items.append(item)
        else:
            case_items.append(item)
    cases = [
        _apply_defaults(case, defaults)
        for item in case_items
        if (case := _check(Case, item, "id", problems)) is not None
    ]
    default_agents = defaults.agents
    overlays = [
        overlay
        for item in overlay_items
        if (
            overlay := _check(
                Overlay,
                {"agents": default_agents, **item}
                if default_agents is not None and isinstance(item, dict)
                else item,
                "extends",
                problems,
            )
        )
        is not None
    ]
    if problems:
        raise CaseSchemaError(path, problems)
    return cases, overlays


HASHED_FILE_FIELDS = ("key", "sha256", "context")


def content_hash(case: Case, files: list[dict[str, str]] | None = None) -> str:
    hashed_files = [
        {field: entry[field] for field in HASHED_FILE_FIELDS if field in entry}
        for entry in files or []
    ]
    body = {
        "input": {**case.input, "files": hashed_files},
        "metadata": {
            "id": case.id,
            "tags": case.tags,
            "expected": case.expected,
            "evaluators": case.evaluators.as_metadata(),
        },
    }
    encoded = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def overlay_hash(overlay: Overlay) -> str:
    evaluators: dict[str, Any] = {"custom": overlay.evaluators.custom}
    trajectory = overlay.evaluators.trajectory.as_metadata()
    if trajectory:
        evaluators["trajectory"] = trajectory
    body = {
        "extends": overlay.extends,
        "agents": overlay.agents,
        "tags": overlay.tags,
        "evaluators": evaluators,
    }
    encoded = json.dumps(body, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]
