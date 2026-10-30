import json
import tempfile
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from evalkit.api.access import ensure_active, load_actor, require, workspace_ref
from evalkit.api.auth import current_user
from evalkit.api.deps import PgStoreDep, RunnerDepsDep, SessionDep
from evalkit.api.runs import harness_client
from evalkit.contracts.cases import REGISTRY_FILENAME
from evalkit.core.access import can_write
from evalkit.core.agents import explicit, server_may_drive
from evalkit.core.fixtures import load_registries
from evalkit.core.importer import import_dataset, load_files
from evalkit.core.lint import fixture_findings
from evalkit.core.lint import lint as lint_cases
from evalkit.core.runner import RunnerDeps
from evalkit.core.workspace import parse_slug
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.models import User
from evalkit.db.workspaces import WorkspaceStore

router = APIRouter(prefix="/api", tags=["imports"])


def _safe_relative(name: str) -> Path:
    parts = [p for p in Path(name).parts if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts) or Path(name).is_absolute():
        raise HTTPException(status_code=400, detail=f"unsafe upload path: {name!r}")
    return Path(*parts)


async def _save_uploads(files: list[UploadFile], directory: Path) -> list[Path]:
    saved = []
    for upload in files:
        path = directory / _safe_relative(upload.filename or "cases.yaml")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(await upload.read())
        saved.append(path)
    return saved


def _input_schema(raw: str) -> dict:
    try:
        schema = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"input_schema is not valid JSON: {exc}"
        ) from exc
    if not isinstance(schema, dict):
        raise HTTPException(status_code=400, detail="input_schema must be a JSON object")
    return schema


async def _agent_input_schema(deps: RunnerDeps, agent: str) -> dict:
    definition = await deps.agents.get(explicit(parse_slug(agent)))
    if not server_may_drive(definition):
        raise HTTPException(
            status_code=400,
            detail=f"the server does not drive {definition.ref}; evalkit lint --agent "
            "reads its input schema from your laptop and sends it as input_schema",
        )
    client = await harness_client(deps, definition)
    try:
        caps = await client.capabilities()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"harness {agent}: {exc}") from exc
    finally:
        await client.aclose()
    return caps.effective_input_schema


@router.post("/lint")
async def lint_endpoint(
    files: list[UploadFile],
    deps: RunnerDepsDep,
    store: PgStoreDep,
    session: SessionDep,
    request: Request,
    dataset: str | None = None,
    agent: str | None = None,
    input_schema: Annotated[str | None, Form()] = None,
    warn_unused_fixtures: bool = False,
) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        paths = await _save_uploads(files, Path(tmp))
        sourced_cases, overlays, findings = load_files(paths)
        registry = Path(tmp) / REGISTRY_FILENAME
        if registry in paths:
            entries, registry_findings = load_registries([registry])
            findings.extend(registry_findings)
            findings.extend(
                fixture_findings(
                    [case for case, _ in sourced_cases],
                    entries,
                    warn_unused=warn_unused_fixtures,
                )
            )
        other_cases: dict[str, str] | None = None
        if dataset is not None:
            ref = parse_slug(dataset)
            other_cases = await store.workspace_cases(
                await workspace_ref(session, ref.workspace), exclude_dataset=ref.name
            )
        schema = await _agent_input_schema(deps, agent) if agent is not None else None
        if input_schema is not None:
            schema = _input_schema(input_schema)
        known = await RemoteEvaluatorStore(request.app.state.session_factory).names()
        findings.extend(
            lint_cases(
                [case for case, _ in sourced_cases],
                [overlay for overlay, _ in overlays],
                known_custom_evaluators=known,
                other_cases=other_cases,
                input_schema=schema,
            )
        )
    return {
        "findings": [f.__dict__ for f in findings],
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
    }


@router.post("/sync")
async def sync(
    request: Request,
    session: SessionDep,
    store: PgStoreDep,
    dataset: str,
    user: Annotated[User, Depends(current_user)],
    files: list[UploadFile] = File(default_factory=list),
    workspace: str | None = None,
    prune: bool = False,
    sha: str | None = None,
    branch: str | None = None,
    dirty: bool | None = None,
    actor: str | None = None,
) -> dict:
    target = workspace or user.username
    target_ws = await workspace_ref(session, target)
    require(can_write(await load_actor(session, user), target_ws), f"you cannot sync to @{target}")
    ensure_active(target_ws)
    if target_ws.is_target:
        assert target_ws.promoted_from is not None
        source_ws = await WorkspaceStore(request.app.state.session_factory).get_by_id(
            target_ws.promoted_from
        )
        raise HTTPException(
            status_code=409,
            detail=f"{target} is the promotion target of {source_ws.name}; "
            f"sync {source_ws.name} instead",
        )
    if actor is not None and not user.is_admin:
        raise HTTPException(status_code=403, detail="actor override is admin only")
    detail = {
        k: v
        for k, v in {"sha": sha, "branch": branch, "dirty": dirty, "actor": actor}.items()
        if v is not None
    }
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths = await _save_uploads(files, root)
        report = await import_dataset(
            store,
            workspace=target_ws,
            dataset=dataset,
            files=paths,
            full=prune,
            known_custom_evaluators=await RemoteEvaluatorStore(
                request.app.state.session_factory
            ).names(),
            actor_id=user.id,
            detail=detail,
            root=root,
        )
    return report.model_dump()
