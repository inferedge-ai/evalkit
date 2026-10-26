from __future__ import annotations

from typing import NamedTuple


class Reference(NamedTuple):
    name: str
    workspace: str


class BareReference(ValueError):
    def __init__(self, text: str):
        super().__init__(f"use name@workspace, not {text!r}")


def parse_reference(text: str, default_workspace: str) -> Reference:
    name, sep, workspace = text.partition("@")
    if not name or (sep and (not workspace or "@" in workspace)):
        raise ValueError(f"malformed reference: {text!r}")
    return Reference(name, workspace if sep else default_workspace)


def parse_slug(value: str) -> Reference:
    if "@" not in value:
        raise BareReference(value)
    return parse_reference(value, "")


def slug(ref: Reference) -> str:
    return f"{ref.name}@{ref.workspace}"
