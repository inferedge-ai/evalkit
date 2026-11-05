from __future__ import annotations

import importlib.metadata
import re
from enum import StrEnum
from typing import NamedTuple


class ParsedVersion(NamedTuple):
    major: int
    minor: int
    patch: int
    build_id: str | None


class VersionComparison(StrEnum):
    SAME = "same"
    DIFFERENT = "different"
    INCOMPATIBLE = "incompatible"


DISTRIBUTION = "evalkit-py"
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:\.(dev\d+))?(?:\+(.+))?")


def current() -> str:
    return importlib.metadata.version(DISTRIBUTION)


def parse(version: str) -> ParsedVersion:
    match = _VERSION.fullmatch(version)
    if match is None:
        raise ValueError(f"not an evalkit version: {version}")
    major, minor, patch, dev, local = match.groups()
    return ParsedVersion(int(major), int(minor), int(patch), dev or local)


def compare(local: str, remote: str) -> VersionComparison:
    mine, theirs = parse(local), parse(remote)
    if mine.major != theirs.major or (mine.major == 0 and mine.minor != theirs.minor):
        return VersionComparison.INCOMPATIBLE
    if local == remote:
        return VersionComparison.SAME
    return VersionComparison.DIFFERENT
