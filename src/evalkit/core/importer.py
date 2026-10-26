"""One importer code path for `sync`: loads and
validates YAML, lints against the other datasets in the workspace, checks that
every fixture's bytes were uploaded, then hands the cases and overlays to the
store, which diffs by content hash.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import yaml
from pydantic import BaseModel, Field, ValidationError

from evalkit.contracts.cases import (
    REGISTRY_FILENAME,
    Case,
    FixtureEntry,
    Overlay,
    content_hash,
    is_case_yaml,
    load_cases_from_yaml,
)
from evalkit.contracts.enums import DatasetKind, SyncSource
from evalkit.core.access import WorkspaceRef
from evalkit.core.fixtures import load_registries
from evalkit.core.lint import Finding, fixture_findings, has_errors
from evalkit.core.lint import lint as lint_cases
from evalkit.core.overlays import OverlayBlock, incoming_overlay, overlay_clashes
from evalkit.store.base import (
    CarriedReview,
    CaseFile,
    IncomingCase,
    IncomingOverlay,
    SyncResult,
)


class CaseCatalog(Protocol):
    async def workspace_cases(
        self, workspace: WorkspaceRef, *, exclude_dataset: str | None
    ) -> dict[str, str]: ...

    async def overlays_on(
        self, workspace: WorkspaceRef, case_ids: list[str], *, exclude_dataset: str
    ) -> dict[str, list[OverlayBlock]]: ...

    async def known_blobs(self, shas: list[str]) -> set[str]: ...

    async def has_dataset(self, workspace: WorkspaceRef, name: str) -> bool: ...

    async def sync(
        self,
        *,
        workspace: WorkspaceRef,
        dataset: str,
        kind: DatasetKind,
        incoming: list[IncomingCase],
        overlays: list[IncomingOverlay],
        full: bool,
        actor_id: int,
        source: SyncSource,
        detail: dict,
    ) -> SyncResult: ...


class ReviewReport(BaseModel):
    now_draft: list[str] = Field(default_factory=list)
    carried: list[CarriedReview] = Field(default_factory=list)


class ImportReport(BaseModel):
    created: int = 0
    patched: int = 0
    deleted: int = 0
    overlays_applied: int = 0
    findings: list[Finding] = Field(default_factory=list)
    aborted: bool = False
    review: ReviewReport = Field(default_factory=ReviewReport)


def load_files(
    files: list[Path],
) -> tuple[list[tuple[Case, Path]], list[tuple[Overlay, str]], list[Finding]]:
    cases: list[tuple[Case, Path]] = []
    overlays: list[tuple[Overlay, str]] = []
    findings: list[Finding] = []
    for path in files:
        if not is_case_yaml(path):
            continue
        try:
            file_cases, file_overlays = load_cases_from_yaml(path)
        except (ValidationError, ValueError, yaml.YAMLError) as exc:
            findings.append(
                Finding(
                    severity="error",
                    code="E-SCHEMA",
                    case_id=path.name,
                    message=str(exc),
                )
            )
            continue
        cases.extend((case, path) for case in file_cases)
        overlays.extend((ov, path.name) for ov in file_overlays)
    return cases, overlays, findings


def case_files(case: Case, entries: dict[str, FixtureEntry]) -> list[CaseFile]:
    return [
        CaseFile(key=key, sha256=entries[key].sha256, context=entries[key].context)
        for key in case.fixtures
        if key in entries
    ]


async def missing_blobs(
    catalog: CaseCatalog, cases: list[Case], entries: dict[str, FixtureEntry]
) -> list[Finding]:
    wanted = [(case.id, file) for case in cases for file in case_files(case, entries)]
    known = await catalog.known_blobs(sorted({file.sha256 for _, file in wanted}))
    return [
        Finding(
            severity="error",
            code="E-FIXTURE-MISSING",
            case_id=case_id,
            message=(
                f"fixture {file.key!r} (sha256 {file.sha256}) was never uploaded; the evalkit "
                "CLI uploads it from the `fixtures:` directory in evalkit.yaml when you sync"
            ),
        )
        for case_id, file in wanted
        if file.sha256 not in known
    ]


def _path_tags(source_path: Path | None, root: Path | None) -> list[str]:
    if source_path is None or root is None:
        return []
    return list(source_path.resolve().relative_to(root.resolve()).parent.parts)


def incoming_case(
    case: Case, source: str, files: list[CaseFile], extra_tags: list[str]
) -> IncomingCase:
    return IncomingCase(
        case_id=case.id,
        content_hash=content_hash(case, [f.model_dump() for f in files]),
        input=dict(case.input),
        expected=case.expected,
        evaluators=case.evaluators.as_metadata(),
        tags=list(case.tags),
        path_tags=[tag for tag in dict.fromkeys(extra_tags) if tag not in case.tags],
        source=source,
        files=files,
    )


async def import_cases(
    catalog: CaseCatalog,
    *,
    workspace: WorkspaceRef,
    dataset: str,
    kind: DatasetKind,
    sourced_cases: list[tuple[Case, Path | None]],
    overlays: list[tuple[Overlay, str]],
    findings: list[Finding],
    full: bool,
    known_custom_evaluators: set[str],
    actor_id: int,
    source: SyncSource,
    detail: dict,
    entries: dict[str, FixtureEntry] | None = None,
    root: Path | None = None,
) -> ImportReport:
    cases = [case for case, _ in sourced_cases]
    entries = entries or {}
    findings = list(findings)
    findings.extend(
        lint_cases(
            cases,
            [overlay for overlay, _ in overlays],
            known_custom_evaluators=known_custom_evaluators,
            other_cases=await catalog.workspace_cases(workspace, exclude_dataset=dataset),
        )
    )
    existing = await catalog.overlays_on(
        workspace, [overlay.extends for overlay, _ in overlays], exclude_dataset=dataset
    )
    findings.extend(overlay_clashes([overlay for overlay, _ in overlays], existing))
    findings.extend(fixture_findings(cases, entries, warn_unused=False))
    if has_errors(findings):
        return ImportReport(findings=findings, aborted=True)
    findings.extend(await missing_blobs(catalog, cases, entries))
    if has_errors(findings):
        return ImportReport(findings=findings, aborted=True)
    if not sourced_cases and not overlays and not await catalog.has_dataset(workspace, dataset):
        return ImportReport(findings=findings)
    result = await catalog.sync(
        workspace=workspace,
        dataset=dataset,
        kind=kind,
        incoming=[
            incoming_case(
                case,
                source=dataset if source_path is None else source_path.name,
                files=case_files(case, entries),
                extra_tags=_path_tags(source_path, root),
            )
            for case, source_path in sourced_cases
        ],
        overlays=[incoming_overlay(overlay, file) for overlay, file in overlays],
        full=full,
        actor_id=actor_id,
        source=source,
        detail=detail,
    )
    return ImportReport(
        created=len(result.created),
        patched=len(result.patched),
        deleted=len(result.removed),
        overlays_applied=result.overlays,
        findings=findings,
        review=ReviewReport(now_draft=result.now_draft, carried=result.carried),
    )


async def import_dataset(
    catalog: CaseCatalog,
    *,
    workspace: WorkspaceRef,
    dataset: str,
    files: list[Path],
    full: bool,
    known_custom_evaluators: set[str],
    actor_id: int,
    detail: dict,
    root: Path | None = None,
) -> ImportReport:
    sourced_cases, overlays, findings = load_files(files)
    entries, registry_findings = load_registries([p for p in files if p.name == REGISTRY_FILENAME])
    sourced: list[tuple[Case, Path | None]] = list(sourced_cases)
    return await import_cases(
        catalog,
        workspace=workspace,
        dataset=dataset,
        kind=DatasetKind.SUITE,
        sourced_cases=sourced,
        overlays=overlays,
        findings=findings + registry_findings,
        full=full,
        known_custom_evaluators=known_custom_evaluators,
        actor_id=actor_id,
        source=SyncSource.GIT,
        detail=detail,
        entries=entries,
        root=root,
    )
