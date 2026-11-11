import pytest

from evalkit.core.endpoints import BlockedEndpoint, EndpointRule, check_endpoint

REFUSE = EndpointRule(allow_private=False)
GATEWAY = EndpointRule(allow_private=False, trusted=("http://litellm-svc:4000/v1",))


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/v1",
        "http://10.0.0.5/v1",
        "http://192.168.1.2/v1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/v1",
        "http://[fc00::1]/v1",
        "http://0.0.0.0/v1",
        "http://localhost/v1",
        "http://100.64.0.1/v1",
        "http://[64:ff9b::a00:1]/v1",
    ],
)
async def test_private_addresses_are_refused(url):
    with pytest.raises(BlockedEndpoint, match="allow_private_endpoints"):
        await check_endpoint(url, rule=REFUSE)


async def test_public_addresses_pass():
    await check_endpoint("https://8.8.8.8/v1", rule=REFUSE)


async def test_the_operator_can_allow_private_addresses():
    await check_endpoint("http://169.254.169.254/", rule=EndpointRule(allow_private=True))


async def test_unresolvable_hosts_are_refused():
    with pytest.raises(BlockedEndpoint, match="cannot resolve"):
        await check_endpoint("http://no-such-host.invalid/v1", rule=REFUSE)


@pytest.mark.parametrize(
    "url", ["http://litellm-svc:4000", "http://LiteLLM-Svc:4000/v1/chat/completions"]
)
async def test_a_trusted_origin_passes_without_resolving(url):
    await check_endpoint(url, rule=GATEWAY)


async def test_a_trusted_default_port_matches_either_spelling():
    await check_endpoint("http://gateway/v1", rule=EndpointRule(False, ("http://gateway:80",)))
    await check_endpoint("https://gateway:443/v1", rule=EndpointRule(False, ("https://gateway",)))


@pytest.mark.parametrize(
    "url",
    [
        "https://litellm-svc:4000/v1",
        "http://litellm-svc:4001/v1",
        "http://litellm-svc/v1",
        "http://litellm-svc:4000@127.0.0.1:9/v1",
    ],
)
async def test_only_the_trusted_origin_passes(url):
    with pytest.raises(BlockedEndpoint):
        await check_endpoint(url, rule=GATEWAY)
