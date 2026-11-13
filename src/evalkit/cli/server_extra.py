import importlib.metadata
import re
from collections.abc import Iterable

from evalkit import version

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_SERVER_MARKER = re.compile(r"""extra\s*==\s*["']server["']""")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def list_server_requirements() -> list[str]:
    requires = importlib.metadata.requires(version.DISTRIBUTION) or []
    names = []
    for requirement in requires:
        name, _, marker = requirement.partition(";")
        match = _NAME.match(name.strip())
        if match and _SERVER_MARKER.search(marker):
            names.append(normalize_name(match.group()))
    return names


def list_installed() -> set[str]:
    return {
        normalize_name(dist.metadata["Name"])
        for dist in importlib.metadata.distributions()
        if dist.metadata["Name"]
    }


def has_server_extra(
    requirements: Iterable[str] | None = None, installed: set[str] | None = None
) -> bool:
    needed = list_server_requirements() if requirements is None else list(requirements)
    present = list_installed() if installed is None else installed
    return bool(needed) and all(name in present for name in needed)
