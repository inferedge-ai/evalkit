from evalkit.core.tenant import resolve_tenant


def test_public_evalkit_has_one_tenant():
    assert resolve_tenant(None, None) == 1
    assert resolve_tenant(object(), {"sub": "anyone"}) == 1
