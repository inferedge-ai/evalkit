import os
from collections.abc import Iterable, Mapping
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from evalkit.contracts.settings import JudgeSettings
from evalkit.core.evaluators.base import BUILTIN_EVALUATORS, BuiltinEval
from evalkit.core.judge import JudgeUsage
from evalkit.core.python_eval import PythonEvaluator
from evalkit.store.base import CaseView, ScoreRow

SERVER_EVALUATOR_TIMEOUT_SECONDS = 900


class Location(StrEnum):
    LOCAL = "local"
    SERVER = "server"


class ServerScore(BaseModel):
    rows: list[ScoreRow]
    judge_usage: JudgeUsage = Field(default_factory=JudgeUsage)


class ServerEvaluationRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class ServerEvaluators(Protocol):
    async def score(
        self, experiment_run_id: int, name: str, config: dict[str, Any]
    ) -> ServerScore: ...


def resolve_location(evaluator: BuiltinEval, judge: JudgeSettings | None) -> Location:
    if evaluator.local_only:
        return Location.LOCAL
    if evaluator.uses_judge and judge is not None and judge.base_url is not None:
        return Location.LOCAL
    return Location.SERVER


def configured_builtins(evaluators: dict) -> dict[str, dict]:
    configured: dict[str, dict] = {}
    for family in ("output", "trajectory"):
        for name, config in (evaluators.get(family) or {}).items():
            if name in BUILTIN_EVALUATORS:
                configured[name] = config
    return configured


NO_EXTENSION_EVALUATORS: Mapping[str, PythonEvaluator] = {}


def needs_judge(
    cases: Iterable[CaseView],
    extension_evaluators: Mapping[str, PythonEvaluator] = NO_EXTENSION_EVALUATORS,
) -> bool:
    return any(
        any(
            BUILTIN_EVALUATORS[name].uses_judge
            for name in configured_builtins(case.effective_evaluators)
        )
        or any(
            name in extension_evaluators and extension_evaluators[name].uses_judge
            for name in case.effective_evaluators.get("custom") or {}
        )
        for case in cases
    )


def server_evaluator_timeout() -> float:
    return float(
        os.environ.get("EVALKIT_SERVER_EVALUATOR_TIMEOUT", SERVER_EVALUATOR_TIMEOUT_SECONDS)
    )
