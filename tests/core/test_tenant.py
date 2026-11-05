from evalkit.core.tenant import resolve_tenant


async def test_the_public_repo_resolves_every_request_to_tenant_1():
    assert await resolve_tenant(None, None) == 1
    assert await resolve_tenant(object(), {"sub": "anyone"}) == 1
