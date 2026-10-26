from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from evalkit.contracts.cases import FixtureEntry, load_registry
from evalkit.core.lint import Finding
from evalkit.store.base import CaseFile


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_registries(paths: list[Path]) -> tuple[dict[str, FixtureEntry], list[Finding]]:
    entries: dict[str, FixtureEntry] = {}
    findings: list[Finding] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            registry = load_registry(path)
        except (ValidationError, yaml.YAMLError) as exc:
            findings.append(
                Finding(severity="error", code="E-SCHEMA", case_id=path.name, message=str(exc))
            )
            continue
        for entry in registry.fixtures:
            if entry.key in entries:
                findings.append(
                    Finding(
                        severity="error",
                        code="E-FIXTURE-DUPLICATE-KEY",
                        case_id=entry.key,
                        message=f"fixture key {entry.key!r} is declared more than once ({path})",
                    )
                )
                continue
            entries[entry.key] = entry
    return entries, findings


ATTACHED_FILES_HEADER = "Attached files:"


def compose_input(case_input: dict[str, Any], files: list[CaseFile]) -> dict[str, Any]:
    out = dict(case_input)
    question = str(case_input.get("question", ""))
    if not files or question.startswith(ATTACHED_FILES_HEADER):
        return out
    lines = [ATTACHED_FILES_HEADER] + [f"- {f.key}: {f.context}" for f in files]
    out["question"] = "\n".join(lines) + "\n\n" + question
    return out
