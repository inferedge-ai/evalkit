from types import SimpleNamespace

from evalkit.config import RunnerConfig
from evalkit.core.runner import RunnerDeps, missing_local_keys
from evalkit.credentials import CredentialRegistry, CredentialsConfig
from tests.fake_agents import FakeAgents, FakeEvaluators, fake_agent
from tests.fake_blob import DictBlobStore


def deps(registry: CredentialRegistry) -> RunnerDeps:
    return RunnerDeps(
        cfg=RunnerConfig(),
        store=None,  # type: ignore[arg-type]
        blob_store=DictBlobStore(),
        agents=FakeAgents(),
        evaluators=FakeEvaluators(),
        credentials=registry,
    )


async def test_local_runs_name_every_keyless_endpoint():
    registry = CredentialRegistry(
        CredentialsConfig(evaluators={"required_tool_calls@canonical": "env:K"}), {"K": "k"}
    )
    used = SimpleNamespace(
        effective_evaluators={"custom": {"required_tool_calls": {}, "broken_eval": {}}}
    )
    unused = SimpleNamespace(effective_evaluators={})
    lines = await missing_local_keys(deps(registry), fake_agent(), [used, unused])  # type: ignore[list-item]
    assert lines == [
        "no credentials.agents entry for fake@canonical in evalkit.yaml; "
        "calling the harness without x-api-key",
        "no credentials.evaluators entry for broken_eval@canonical in evalkit.yaml; "
        "calling it without x-api-key",
    ]


async def test_evaluators_no_case_uses_are_not_named():
    registry = CredentialRegistry(CredentialsConfig(), {})
    lines = await missing_local_keys(deps(registry), fake_agent(), [])
    assert lines == [
        "no credentials.agents entry for fake@canonical in evalkit.yaml; "
        "calling the harness without x-api-key"
    ]
