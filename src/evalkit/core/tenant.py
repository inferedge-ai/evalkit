import re
from typing import Any

DEFAULT_TENANT_ID = "default"
TENANT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}")


class BadTenantId(ValueError):
    pass


def check_tenant_id(tenant_id: str) -> str:
    if not TENANT_ID_PATTERN.fullmatch(tenant_id):
        raise BadTenantId(f"tenant id {tenant_id!r} is not 1-64 letters, digits, '-' or '_'")
    return tenant_id


def resolve_tenant(request: Any, claims: dict | None) -> str:
    return DEFAULT_TENANT_ID
