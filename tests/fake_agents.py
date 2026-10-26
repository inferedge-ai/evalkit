import json
from pathlib import Path

from evalkit.contracts.enums import EvaluatorKind, TrajectorySource
from evalkit.contracts.esf import MappingDocument
from evalkit.contracts.settings import AgentSettings, RemoteEvaluatorSettings
from evalkit.core.agents import AgentDefinition, UnknownAgent
from evalkit.core.remote_evaluators import RemoteEvaluator
from evalkit.credentials import CredentialRegistry, CredentialsConfig

FAKE_HARNESS_URL = "http://fake/evalkit/v1"
MAPPINGS_DIR = Path(__file__).parent.parent / "skills" / "connect-traces" / "references"
FAKE_SETTINGS = AgentSettings(poll_interval_seconds=0, run_timeout_seconds=5, max_concurrent_runs=2)
JUDGE_URL = "http://judge.test"
TEST_CREDENTIALS_CONFIG = CredentialsConfig(
    agents={
        "fake@canonical": "env:FAKE_AGENT_KEY",
        "fake@akshat": "env:FAKE_AGENT_KEY",
        "other@canonical": "env:FAKE_AGENT_KEY",
    },
    evaluators={
        "required_tool_calls@canonical": "env:CUSTOM_EVAL_KEY",
        "broken_eval@canonical": "env:CUSTOM_EVAL_KEY",
    },
    judges={JUDGE_URL: "env:JUDGE_KEY", "http://j": "env:JUDGE_KEY"},
)
TEST_ENV = {"FAKE_AGENT_KEY": "client-key", "CUSTOM_EVAL_KEY": "client-key", "JUDGE_KEY": "jk"}


def fake_agent(
    name: str = "fake",
    workspace: str = "canonical",
    *,
    shared: bool = True,
    harness_url: str | None = FAKE_HARNESS_URL,
    trajectory_source: TrajectorySource = TrajectorySource.ATIF,
    settings: AgentSettings = FAKE_SETTINGS,
    agent_id: int = 1,
) -> AgentDefinition:
    return AgentDefinition(
        id=agent_id,
        name=name,
        workspace=workspace,
        workspace_is_shared=shared,
        harness_url=harness_url,
        trajectory_source=trajectory_source,
        repo_url=None,
        settings=settings,
    )


class FakeAgents:
    def __init__(self, *agents: AgentDefinition):
        defaults = (fake_agent(), fake_agent(workspace="akshat", shared=False, agent_id=2))
        self.agents = {a.ref: a for a in agents or defaults}

    async def get(self, ref: str) -> AgentDefinition:
        if ref not in self.agents:
            raise UnknownAgent(ref)
        return self.agents[ref]


def fake_credentials(home: str | None = None) -> CredentialRegistry:
    return CredentialRegistry(TEST_CREDENTIALS_CONFIG, TEST_ENV, home=home)


def reference_mapping(name: str = "openinference") -> dict:
    return json.loads((MAPPINGS_DIR / f"{name}.json").read_text())


FAKE_EVALUATORS = {
    "required_tool_calls": RemoteEvaluatorSettings(
        endpoint="http://custom.test/rtc", timeout_seconds=2
    ),
    "broken_eval": RemoteEvaluatorSettings(endpoint="http://custom.test/broken", timeout_seconds=2),
}


class FakeEvaluators:
    def __init__(self, by_workspace: dict[str, dict[str, RemoteEvaluator]] | None = None):
        self.by_workspace = (
            by_workspace
            if by_workspace is not None
            else {
                "canonical": {
                    name: RemoteEvaluator(
                        name=name,
                        workspace="canonical",
                        kind=EvaluatorKind.SCORE,
                        settings=settings,
                    )
                    for name, settings in FAKE_EVALUATORS.items()
                }
            }
        )

    async def in_workspace(self, workspace: str) -> dict[str, RemoteEvaluator]:
        return self.by_workspace.get(workspace, {})


REFERENCE = {
    name: MappingDocument.model_validate(reference_mapping(name))
    for name in ("openinference", "gen_ai")
}
