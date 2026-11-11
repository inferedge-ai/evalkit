import pytest

from evalkit.api.deps import get_allow_private_endpoints
from tests.api.native_api import api_env
from tests.fake_harness import FakeHarness
from tests.native import wait_for

RUN = {"dataset": "finance@canonical", "agent": "fake@canonical"}
STORED = "http://127.0.0.1:9/v1"
METADATA = "http://169.254.169.254/"


@pytest.fixture
async def api(tmp_path, stub_judges, session_factory):
    async with api_env(tmp_path, session_factory, FakeHarness()) as (client, _env):
        yield client


async def test_a_host_refuses_private_addresses_the_server_allows(api):
    exp_id = (await api.post("/api/experiments", json=RUN)).json()["id"]
    await wait_for(api, exp_id)
    evaluator = {"workspace": "canonical", "name": "remote", "kind": "score"}
    await api.post("/api/evaluators", json={**evaluator, "settings": {"endpoint": STORED}})
    source = {"name": "phx", "provider": "phoenix", "url": STORED}
    assert (await api.post("/api/trace-sources", json=source)).status_code == 200
    moved = await api.patch("/api/agents/fake@canonical", json={"harness_url": STORED})
    assert moved.status_code == 200, moved.text

    api._transport.app.dependency_overrides[get_allow_private_endpoints] = lambda: False  # type: ignore[attr-defined]
    requests = [
        ("POST", "/api/agents", {"workspace": "canonical", "name": "m", "harness_url": METADATA}),
        ("PATCH", "/api/agents/fake@canonical", {"harness_url": METADATA}),
        ("POST", "/api/evaluators", {**evaluator, "name": "m", "settings": {"endpoint": METADATA}}),
        ("PATCH", "/api/evaluators/remote@canonical", {"settings": {"endpoint": METADATA}}),
        ("POST", "/api/llm-providers", {"name": "m", "base_url": METADATA}),
        ("PATCH", "/api/llm-providers/judge", {"base_url": METADATA}),
        ("POST", "/api/trace-sources", {**source, "name": "m", "url": METADATA}),
        ("PATCH", "/api/trace-sources/phx", {"url": METADATA}),
        ("POST", "/api/experiments", RUN),
        ("POST", f"/api/experiments/{exp_id}/evaluate", None),
        ("POST", f"/api/experiments/{exp_id}/resume", None),
        ("POST", "/api/agents/fake@canonical/check", None),
    ]
    for method, path, body in requests:
        refused = await api.request(method, path, json=body)
        assert refused.status_code == 400, (method, path, refused.text)
        assert "private address" in refused.json()["detail"], (method, path)
