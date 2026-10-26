from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel

from evalkit.contracts.enums import TrajectorySource
from evalkit.contracts.settings import AgentSettings
from evalkit.core.workspace import Reference


class AgentDefinition(BaseModel):
    id: int
    name: str
    workspace: str
    workspace_is_shared: bool
    harness_url: str | None
    trajectory_source: TrajectorySource
    repo_url: str | None
    settings: AgentSettings
    archived: bool = False

    @property
    def ref(self) -> str:
        return f"{self.name}@{self.workspace}"

    @property
    def reference(self) -> Reference:
        return Reference(self.name, self.workspace)

    @property
    def is_otel(self) -> bool:
        return self.trajectory_source is TrajectorySource.OTEL


class Driver(StrEnum):
    SERVER = "server"
    CLI = "cli"
    SDK = "sdk"


def driver(agent: AgentDefinition) -> Driver:
    if agent.harness_url is None:
        return Driver.SDK
    if agent.workspace_is_shared:
        return Driver.SERVER
    return Driver.CLI


def server_may_drive(agent: AgentDefinition) -> bool:
    return driver(agent) is Driver.SERVER


class UnknownAgent(KeyError):
    def __init__(self, ref: str):
        super().__init__(f"unknown agent: {ref} — create it with evalkit agents create")
        self.ref = ref


class AgentLookup(Protocol):
    async def get(self, ref: str) -> AgentDefinition: ...


def explicit(ref: Reference) -> str:
    return f"{ref.name}@{ref.workspace}"
