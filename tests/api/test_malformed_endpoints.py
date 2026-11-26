import pytest

from evalkit.api.access import build_tenant_stores
from evalkit.api.server_keys import server_keys
from evalkit.contracts.enums import TraceProvider
from evalkit.core.endpoints import BlockedEndpoint, EndpointRule
from tests.api.native_api import api_env, login_as
from tests.fake_harness import FakeHarness

TYPO = "https:/host.example.com"


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        yield client


@pytest.mark.parametrize("workspace", ["canonical", "alice"])
async def test_an_agent_or_evaluator_url_without_a_host_is_refused_in_every_workspace(
    api, workspace
):
    client = api if workspace == "canonical" else await login_as(api, "alice", "password")
    requests = [
        ("/api/agents", {"workspace": workspace, "name": "typo", "harness_url": TYPO}),
        (
            "/api/evaluators",
            {
                "workspace": workspace,
                "name": "typo",
                "kind": "score",
                "settings": {"endpoint": TYPO},
            },
        ),
    ]
    for path, body in requests:
        refused = await client.post(path, json=body)
        assert refused.status_code == 400, (path, refused.text)
        assert "has no host" in refused.json()["detail"]


async def test_an_agent_on_a_trace_source_saved_without_a_host_names_the_source(api):
    stores = build_tenant_stores(api.app.state, "default")
    await stores.sources.create("phx", TraceProvider.PHOENIX, TYPO, None)
    body = {
        "workspace": "canonical",
        "name": "traced",
        "trajectory_source": "otel",
        "settings": {"trace_source": "phx", "project": "p"},
    }
    refused = await api.post("/api/agents", json=body)
    assert refused.status_code == 400, refused.text
    detail = refused.json()["detail"]
    assert "has no host" in detail and "evalkit trace-sources edit phx --url" in detail


async def test_starting_a_run_checks_the_shared_evaluators_it_will_call(api):
    body = {"workspace": "canonical", "name": "remote", "kind": "score"}
    settings = {"endpoint": "http://127.0.0.1:19000"}
    created = await api.post("/api/evaluators", json={**body, "settings": settings})
    assert created.status_code == 200, created.text
    stores = build_tenant_stores(api.app.state, "default")
    agent = (await stores.agents.get("fake@canonical")).model_copy(update={"harness_url": None})
    await server_keys(stores, agent, None, rule=EndpointRule(allow_private=True))
    refuse = EndpointRule(allow_private=False, trusted=("http://custom.test",))
    with pytest.raises(BlockedEndpoint, match="evaluator remote@canonical: .* private address"):
        await server_keys(stores, agent, None, rule=refuse)
