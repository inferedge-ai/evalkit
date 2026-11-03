from typing import Any

DEFAULT_TENANT_ID = 1


def resolve_tenant(request: Any, claims: dict | None) -> int:
    return DEFAULT_TENANT_ID
