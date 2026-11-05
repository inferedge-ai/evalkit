import pytest

from evalkit.core.tenant import DEFAULT_TENANT_ID, BadTenantId, check_tenant_id, resolve_tenant


def test_public_evalkit_has_one_tenant():
    assert resolve_tenant(None, None) == DEFAULT_TENANT_ID == "default"
    assert resolve_tenant(object(), {"sub": "anyone"}) == "default"


@pytest.mark.parametrize("tenant_id", ["default", "ORG-7f3a", "a_b-9", "x" * 64])
def test_a_tenant_id_of_letters_digits_dash_and_underscore_passes(tenant_id):
    assert check_tenant_id(tenant_id) == tenant_id


@pytest.mark.parametrize("tenant_id", ["", "x" * 65, "../etc", "a/b", "org id", "ORG.1"])
def test_any_other_tenant_id_is_refused(tenant_id):
    with pytest.raises(BadTenantId):
        check_tenant_id(tenant_id)
