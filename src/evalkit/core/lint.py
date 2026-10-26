"""Deterministic quality lint over validated eval cases — no LLM.

Severity is calibrated to confidence and blast radius: errors block sync and
fire only where the case is genuinely broken; warnings are heuristics that
never block. evalkit knows no client tools, so the jargon check looks for
tool-ish vocabulary patterns (mcp__ prefixes, double-underscore tokens,
snake_case call names) rather than a known-tool list.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from evalkit.contracts.cases import Case, FixtureEntry, Overlay
from evalkit.contracts.harness import input_errors

_DATE_ANCHOR = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|\d{1,2}[-/ ](?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-/ ]?\d{2,4}"
    r"|\b(?:january|february|march|april|may|june|july|august|september|october"
    r"|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4}"
    r"|\b20\d{2}\b"
    r"|\bFY\s?\d{2,4}\b"
    r"|\bQ[1-4]\b"
    r"|\bas of\b"
    r"|\bas on\b"
    r"|\bbetween\b.{1,40}\band\b",
    re.IGNORECASE,
)

_NUMERIC_VALUE = re.compile(
    r"\d+\.\d+|\d+\s*%|[₹$€£]\s*\d|\b(?:million|billion|crore|crores|lakh|lakhs|bps)\b",
    re.IGNORECASE,
)

_DOUBLE_UNDERSCORE_TOKEN = re.compile(r"\b\w+__\w+\b")
_SNAKE_CALL = re.compile(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\s*\(")


@dataclass
class Finding:
    severity: Literal["error", "warning"]
    code: str
    case_id: str
    message: str


def _blackbox_texts(case: Case) -> list[tuple[str, str]]:
    texts: list[tuple[str, str]] = [("input.question", str(case.input.get("question", "")))]
    rubrics = case.evaluators.output.answer_rubrics
    if rubrics is not None:
        for r in rubrics.rubrics:
            texts.append(("answer_rubrics.rubric", r.rubric))
    tr = case.evaluators.trajectory.trajectory_rubrics
    if tr is not None:
        for rubric in tr.rubrics:
            texts.append(("trajectory_rubrics.rubric", rubric))
    pj = case.evaluators.output.presentation_judge
    if pj is not None:
        for pr in pj.rubrics:
            texts.append(("presentation_judge.rubric", pr.rubric or pr.forbid or ""))
    return texts


def _jargon_hits(text: str) -> set[str]:
    hits: set[str] = set()
    if "mcp__" in text.lower():
        hits.add("mcp__ prefix")
    hits.update(_DOUBLE_UNDERSCORE_TOKEN.findall(text))
    hits.update(m.rstrip("( \t") for m in _SNAKE_CALL.findall(text))
    return hits


def _e_jargon(case: Case) -> list[Finding]:
    findings = []
    for surface, text in _blackbox_texts(case):
        hits = _jargon_hits(text)
        if hits:
            findings.append(
                Finding(
                    severity="error",
                    code="E-JARGON",
                    case_id=case.id,
                    message=(
                        f"{surface} leaks agent internals ({', '.join(sorted(hits))}) "
                        "— black-box surfaces must stay free of tool/skill names; "
                        "move tool checks into tool_trajectory_match / required_tools_called "
                        "or a custom evaluator"
                    ),
                )
            )
    return findings


def _e_reserved_tags(case_id: str, tags: list[str]) -> list[Finding]:
    return [
        Finding(
            severity="error",
            code="E-RESERVED-TAG",
            case_id=case_id,
            message=(
                f"tag '{t}' is reserved for run-time case selection "
                "(the working split) — pick another tag name"
            ),
        )
        for t in tags
        if t.startswith("working:")
    ]


def _w_custom_unknown(
    case_id: str, custom: dict, known_custom_evaluators: set[str]
) -> list[Finding]:
    return [
        Finding(
            severity="warning",
            code="W-CUSTOM-UNKNOWN",
            case_id=case_id,
            message=(
                f"custom evaluator '{name}' is not a remote evaluator here: create it with "
                "evalkit evaluators create, or pass a Python evaluator with this name to "
                "run_experiment"
            ),
        )
        for name in sorted(set(custom) - known_custom_evaluators)
    ]


def _w_undated(case: Case) -> list[Finding]:
    rubrics = case.evaluators.output.answer_rubrics
    if rubrics is None:
        return []
    rubric_text = " ".join(r.rubric for r in rubrics.rubrics)
    if not _NUMERIC_VALUE.search(rubric_text):
        return []
    if _DATE_ANCHOR.search(str(case.input.get("question", "")) + " " + rubric_text):
        return []
    return [
        Finding(
            severity="warning",
            code="W-UNDATED",
            case_id=case.id,
            message=(
                "rubrics pin numeric values but neither the input nor the rubrics "
                "carry a date anchor — pinned ground truth will drift"
            ),
        )
    ]


def _w_thin_rubrics(case: Case) -> list[Finding]:
    rubrics = case.evaluators.output.answer_rubrics
    if rubrics is None or len(rubrics.rubrics) >= 2:
        return []
    return [
        Finding(
            severity="warning",
            code="W-THIN-RUBRICS",
            case_id=case.id,
            message="answer_rubrics carries a single rubric — thin coverage for a whole case",
        )
    ]


def fixture_findings(
    cases: list[Case], entries: dict[str, FixtureEntry], *, warn_unused: bool = True
) -> list[Finding]:
    findings: list[Finding] = []
    used: set[str] = set()
    for case in cases:
        for key in case.fixtures:
            used.add(key)
            if key not in entries:
                findings.append(
                    Finding(
                        severity="error",
                        code="E-FIXTURE-UNKNOWN-KEY",
                        case_id=case.id,
                        message=f"fixture key {key!r} is not declared in fixtures.yaml",
                    )
                )
    for key in entries:
        if warn_unused and key not in used:
            findings.append(
                Finding(
                    severity="warning",
                    code="W-FIXTURE-UNUSED",
                    case_id=key,
                    message=f"fixture {key!r} is declared but no case references it",
                )
            )
    return findings


def lint(
    cases: list[Case],
    overlays: list[Overlay],
    *,
    known_custom_evaluators: set[str],
    other_cases: Mapping[str, str] | None = None,
    input_schema: dict | None = None,
) -> list[Finding]:
    findings: list[Finding] = []

    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            findings.append(
                Finding(
                    severity="error",
                    code="E-DUP-ID",
                    case_id=case.id,
                    message="duplicate case id in this import",
                )
            )
        seen.add(case.id)
    seen_targets: set[str] = set()
    for overlay in overlays:
        if overlay.extends in seen_targets:
            findings.append(
                Finding(
                    severity="error",
                    code="E-DUP-ID",
                    case_id=overlay.extends,
                    message="multiple overlays extend the same case in this import",
                )
            )
        seen_targets.add(overlay.extends)

    for case in cases:
        findings.extend(_e_jargon(case))
        findings.extend(_e_reserved_tags(case.id, case.tags))
        findings.extend(_w_custom_unknown(case.id, case.evaluators.custom, known_custom_evaluators))
        findings.extend(_w_undated(case))
        findings.extend(_w_thin_rubrics(case))

    if input_schema is not None:
        for case in cases:
            for error in input_errors(input_schema, case.input):
                findings.append(
                    Finding(severity="error", code="E-INPUT-SCHEMA", case_id=case.id, message=error)
                )

    for overlay in overlays:
        findings.extend(_e_reserved_tags(overlay.extends, overlay.tags))
        findings.extend(
            _w_custom_unknown(overlay.extends, overlay.evaluators.custom, known_custom_evaluators)
        )
        if other_cases is not None and overlay.extends not in other_cases:
            findings.append(
                Finding(
                    severity="error",
                    code="E-OVERLAY-TARGET",
                    case_id=overlay.extends,
                    message="overlay extends a case id no other dataset in this workspace holds",
                )
            )
    return findings


def has_errors(findings: list[Finding]) -> bool:
    return any(f.severity == "error" for f in findings)
