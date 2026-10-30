import io
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from evalkit.api.deps import (
    AgentStoreDep,
    RunnerDepsDep,
    SessionDep,
    SessionReaderDep,
    SpanStoreDep,
)
from evalkit.api.download import content_disposition
from evalkit.api.runs import BrowserRun, agent_or_404, harness_client, resolve_runs
from evalkit.contracts.atif import TrajectoryValidationError, validate_trajectory
from evalkit.core.agents import AgentDefinition, server_may_drive
from evalkit.core.costmap import load_cost_map
from evalkit.core.harness import HarnessClient
from evalkit.core.paths import UnsafePathError
from evalkit.core.pull import (
    MAX_PULL,
    RunBundle,
    bundle_files,
    index_row,
    run_body,
)
from evalkit.core.runner import RunnerDeps, stored_trajectory_doc
from evalkit.core.trajectory import backfill_step_costs
from evalkit.db.runs import (
    RunSource,
    agent_run,
    eval_context,
    experiment_run_ids,
)
from evalkit.db.sessions import DbSessionReader
from evalkit.store.base import AgentRunRecord

router = APIRouter(prefix="/api/agents", tags=["agents"])

UNREACHABLE = "only the CLI that runs this agent can reach its harness"


class PullRequest(BaseModel):
    source: RunSource = "all"
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=MAX_PULL, ge=1, le=MAX_PULL)
    offset: int = Field(default=0, ge=0)
    run_ids: list[str] | None = Field(default=None, min_length=1, max_length=MAX_PULL)
    experiment_id: int | None = None

    @model_validator(mode="after")
    def _one_selection(self) -> "PullRequest":
        if self.run_ids is not None and self.experiment_id is not None:
            raise ValueError("pass run_ids or experiment_id, not both")
        return self


@dataclass
class PullContext:
    agent: AgentDefinition
    deps: RunnerDeps
    sessions: DbSessionReader
    harness: HarnessClient | None
    cost_map: dict


async def _trajectory(
    ctx: PullContext, run: BrowserRun, stored: AgentRunRecord | None
) -> dict | None:
    if stored is not None:
        doc, _ = await stored_trajectory_doc(ctx.deps.blob_store, ctx.agent.id, stored)
        if doc is not None:
            return doc
    if ctx.agent.is_otel:
        result = await ctx.sessions.read(ctx.agent.ref, run.run_id)
        if result is None or result.trajectory is None:
            raise LookupError("no spans for this session")
        return result.trajectory
    if ctx.harness is None:
        raise LookupError(UNREACHABLE)
    return (await ctx.harness.trajectory(run.run_id)).model_dump(exclude_none=True)


async def _bundle(ctx: PullContext, session: AsyncSession, run: BrowserRun) -> RunBundle:
    stored = await agent_run(session, ctx.agent.id, run.run_id)
    context = await eval_context(session, ctx.agent.id, run.run_id)
    await session.commit()
    bundle = RunBundle(
        run_id=run.run_id,
        source=run.source,
        status=run.status,
        created_at=run.created_at,
        title=run.title,
        run=run_body(run.run_id, run.source, stored, context),
        eval=context,
    )
    try:
        doc = await _trajectory(ctx, run, stored)
        if doc is not None:
            trajectory = validate_trajectory(doc)
            backfill_step_costs(trajectory, ctx.cost_map)
            bundle.trajectory = trajectory.model_dump(exclude_none=True)
    except (httpx.HTTPError, TrajectoryValidationError, LookupError, UnsafePathError) as exc:
        bundle.pull_error = str(exc)
    return bundle


async def _select(
    record: AgentDefinition, body: PullRequest, deps, spans, session
) -> tuple[list[BrowserRun], int, str | None]:
    if body.run_ids is not None:
        page = await resolve_runs(record, deps, spans, session, run_ids=body.run_ids)
        known = {run.run_id: run for run in page.runs}
        runs = [known.get(run_id) or BrowserRun(run_id=run_id) for run_id in body.run_ids]
        return runs, len(runs), None
    if body.experiment_id is not None:
        agent_id, ids, total = await experiment_run_ids(
            session, body.experiment_id, limit=body.limit, offset=body.offset
        )
        if agent_id != record.id:
            raise HTTPException(
                status_code=404,
                detail=f"experiment {body.experiment_id} did not run agent {record.ref}",
            )
        if not ids:
            return [], total, None
        picked, _, _ = await _select(record, PullRequest(run_ids=ids), deps, spans, session)
        return picked, total, None
    page = await resolve_runs(
        record,
        deps,
        spans,
        session,
        source=body.source,
        since=body.since,
        until=body.until,
        limit=body.limit,
        offset=body.offset,
    )
    return page.runs, page.total, page.harness_note


@router.post("/{agent}/runs/pull")
async def pull_runs(
    agent: str,
    body: PullRequest,
    deps: RunnerDepsDep,
    agents: AgentStoreDep,
    spans: SpanStoreDep,
    sessions: SessionReaderDep,
    session: SessionDep,
) -> Response:
    record = await agent_or_404(agents, agent)
    selected, total, note = await _select(record, body, deps, spans, session)
    await session.commit()
    harness = await harness_client(deps, record) if server_may_drive(record) and selected else None
    ctx = PullContext(
        agent=record,
        deps=deps,
        sessions=sessions,
        harness=harness,
        cost_map=load_cost_map(deps.cfg.cost_map),
    )
    bundles = []
    try:
        for run in selected:
            bundles.append(await _bundle(ctx, session, run))
    finally:
        if ctx.harness is not None:
            await ctx.harness.aclose()
    index = {
        "agent": record.ref,
        "pulled_at": datetime.now(UTC).isoformat(),
        "filters": body.model_dump(mode="json"),
        "total_matching": total,
        "note": note,
        "runs": [index_row(bundle) for bundle in bundles],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.json", json.dumps(index, indent=2))
        for bundle in bundles:
            for path, data in bundle_files(bundle).items():
                archive.writestr(path, data)
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers=content_disposition(f"{record.ref}-runs.zip"),
    )
