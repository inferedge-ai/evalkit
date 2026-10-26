from typing import Literal, Protocol

from pydantic import BaseModel

from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.settings import RemoteEvaluatorSettings


class RemoteEvaluator(BaseModel):
    name: str
    workspace: str
    kind: EvaluatorKind
    settings: RemoteEvaluatorSettings
    archived: bool = False

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.workspace}"

    @property
    def wire_kind(self) -> Literal["evaluator", "gauge"]:
        return "evaluator" if self.kind is EvaluatorKind.SCORE else "gauge"


class EvaluatorLookup(Protocol):
    async def in_workspace(self, workspace: str) -> dict[str, RemoteEvaluator]: ...


class UnknownEvaluator(KeyError):
    def __init__(self, ref: str):
        super().__init__(f"unknown evaluator: {ref} — create it with evalkit evaluators create")
