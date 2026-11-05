from typing import Any

DEFAULT_TENANT_ID = 1


async def resolve_tenant(request: Any, claims: dict | None) -> int | None:
    return DEFAULT_TENANT_ID
