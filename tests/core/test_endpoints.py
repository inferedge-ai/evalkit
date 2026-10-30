import pytest

from evalkit.core.endpoints import BlockedEndpoint, check_endpoint


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
        await check_endpoint(url, allow_private=False)


async def test_public_addresses_pass():
    await check_endpoint("https://8.8.8.8/v1", allow_private=False)


async def test_the_operator_can_allow_private_addresses():
    await check_endpoint("http://169.254.169.254/", allow_private=True)


async def test_unresolvable_hosts_are_refused():
    with pytest.raises(BlockedEndpoint, match="cannot resolve"):
        await check_endpoint("http://no-such-host.invalid/v1", allow_private=False)
