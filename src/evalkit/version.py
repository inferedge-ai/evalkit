from __future__ import annotations

import importlib.metadata
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


def current() -> str:
    return importlib.metadata.version("evalkit")


def parse(version: str) -> ParsedVersion:
    public, _, local = version.partition("+")
    major, minor, patch = (int(part) for part in public.split("."))
    return ParsedVersion(major, minor, patch, local or None)


def compare(local: str, remote: str) -> VersionComparison:
    mine, theirs = parse(local), parse(remote)
    if mine.major != theirs.major or (mine.major == 0 and mine.minor != theirs.minor):
        return VersionComparison.INCOMPATIBLE
    if local == remote:
        return VersionComparison.SAME
    return VersionComparison.DIFFERENT


def version_from_wheel_filename(name: str) -> str:
    return name.removesuffix(".whl").split("-")[1]
