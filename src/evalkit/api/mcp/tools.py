import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta, tzinfo
from typing import Any, get_args
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import HTTPException
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.auth import require_scopes
from mcp.types import ToolAnnotations
from starlette.datastructures import State

from evalkit.api import experiments as experiments_api
from evalkit.api import runs as runs_api
from evalkit.api.agents import agent_view
from evalkit.api.mcp import descriptions as d
from evalkit.api.mcp.auth import SCOPE_READ
from evalkit.api.mcp.models import (
    AgentRow,
    BatchMeta,
    DownloadRunFileResponse,
    Evaluation,
    ExperimentRow,
    ExperimentRunRow,
    ExperimentRunsMeta,
    FileRow,
    FileText,
    FindAgentsResponse,
    FindExperimentsResponse,
    FindRunsResponse,
    GetExperimentRunsResponse,
    GetExperimentsResponse,
    GetRunFilesResponse,
    GetRunsResponse,
    GetRunStatsResponse,
    GetTrajectoryResponse,
    ModelCostRow,
    Money,
    Note,
    PagedMeta,
    PlainMeta,
    RunMetrics,
    RunRow,
    StatsMeta,
    StatsRow,
    SubagentRef,
    ToolCallRow,
    TrajectoryMeta,
    TurnRow,
    usd,
)
from evalkit.contracts.atif import Step, Trajectory
from evalkit.contracts.enums import ExperimentStatus
from evalkit.core.agents import AgentDefinition, UnknownAgent
from evalkit.core.costmap import load_cost_map
from evalkit.core.paths import is_safe_run_id
from evalkit.core.run_digest import (
    GROUP_BY_VALUES,
    GroupStats,
    RunDigest,
    StatsInput,
    Turn,
    clip,
    content_text,
    digest,
    group_stats,
    priced_steps,
    turns,
)
from evalkit.core.traces.sessions import SessionTooLarge
from evalkit.core.trajectory import backfill_step_costs
from evalkit.core.workspace import parse_slug
from evalkit.db import experiments as db_experiments
from evalkit.db import scorecard as db_scorecard
from evalkit.db.runs import RunSource, trajectory_keys
from evalkit.db.workspaces import UnknownWorkspace
from evalkit.store.base import UnknownExperiment


def _registration(open_world: bool) -> dict[str, Any]:
    return {
        "annotations": ToolAnnotations(
            read_only_hint=True, destructive_hint=False, open_world_hint=open_world
        ),
        "meta": {"anthropic/alwaysLoad": True},
        "auth": require_scopes(SCOPE_READ),
    }


# Open world: the tool reads the agent's harness live, not only evalkit's store.
READ_TOOL = _registration(open_world=False)
HARNESS_READ_TOOL = _registration(open_world=True)

FIND_RUNS_MAX = 200
FIND_RUNS_LIST_MAX = 5000
GET_RUNS_MAX = 200
STATS_MAX_RUNS = 5000
STEP_PAGE_MAX = 100
FILE_CHARS = 20000
FILE_CHARS_MAX = 200000
EXPERIMENTS_MAX = 100
GET_EXPERIMENTS_MAX = 20
# Per call, so one heavy call cannot queue ahead of a light one; the server-wide
# cap bounds what every call together puts on harnesses and the event loop.
FETCH_CONCURRENCY = 32
FETCH_CONCURRENCY_TOTAL = 64
# Whole call, listing included: under the 60 s load-balancer timeout.
CALL_BUDGET_SECONDS = 45
OVER_BUDGET = "not read within this call's time budget; ask for it again with get_runs"

LOGGER = logging.getLogger(__name__)
RUN_INCLUDES = frozenset({"metrics", "turns", "tool_calls"})
RUN_SOURCES: tuple[str, ...] = get_args(RunSource)
EXPERIMENT_INCLUDES = frozenset({"scorecard"})


STATUS_CODES = {
    400: "invalid_request",
    401: "forbidden",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    413: "invalid_request",
}


def fail(code: str, detail: str, log_level: int = logging.WARNING) -> ToolError:
    return ToolError(f"{code}: {detail}", log_level=log_level)


def translate(exc: Exception) -> ToolError | None:
    if isinstance(exc, HTTPException):
        code = STATUS_CODES.get(exc.status_code, "upstream_failed")
        level = logging.ERROR if exc.status_code == 500 else logging.WARNING
        return fail(code, str(exc.detail), level)
    if isinstance(exc, (UnknownAgent, UnknownExperiment, UnknownWorkspace)):
        return fail("not_found", exc.args[0])
    return None


# FastMCP logs any exception other than ToolError at ERROR before middleware
# sees it, so expected errors become ToolError inside the tool.
def expected_errors[**P, R](tool: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    @functools.wraps(tool)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return await tool(*args, **kwargs)
        except Exception as exc:
            translated = translate(exc)
            if translated is None:
                raise
            raise translated from exc

    return wrapper


def deadline() -> float:
    return asyncio.get_running_loop().time() + CALL_BUDGET_SECONDS


def check_run_ids(run_ids: list[str]) -> None:
    unsafe = [r for r in run_ids if not is_safe_run_id(r)]
    if unsafe:
        raise fail("invalid_run_id", f"run ids must be one plain path segment: {unsafe[:5]}")


def check_agent_id(ref: str) -> str:
    try:
        parse_slug(ref)
    except ValueError as exc:
        raise fail(
            "invalid_agent_id", f"{ref!r}: use the name@workspace id from find_agents"
        ) from exc
    return ref


def check_choice(code: str, value: str, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise fail(code, f"{value!r}; valid: {list(allowed)}")


def check_page(limit: int, offset: int, maximum: int) -> None:
    if not 1 <= limit <= maximum:
        raise fail("invalid_limit", f"limit must be between 1 and {maximum}; got {limit}")
    if offset < 0:
        raise fail("invalid_offset", f"offset must be 0 or greater; got {offset}")


def check_ids(ids: list[Any], maximum: int) -> None:
    if not ids:
        raise fail("no_ids_provided", "pass at least one id")
    if len(ids) > maximum:
        raise fail("too_many_ids", f"at most {maximum} ids per call; got {len(ids)}")


def check_include(include: list[str], allowed: frozenset[str]) -> set[str]:
    unknown = sorted(set(include) - allowed)
    if unknown:
        raise fail("invalid_include", f"{unknown}; valid: {sorted(allowed)}")
    return set(include)


def zone_of(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise fail("invalid_timezone", f"{name} is not an IANA timezone") from exc


def _bound(value: str, zone: tzinfo, *, end: bool) -> datetime:
    try:
        if len(value) == 10:
            day = date.fromisoformat(value)
            moment = datetime(day.year, day.month, day.day, tzinfo=zone)
            return moment + timedelta(days=1) if end else moment
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise fail(
            "invalid_date_format", f"{value!r}: use YYYY-MM-DD or an ISO 8601 timestamp"
        ) from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)


def window(
    start_date: str | None, end_date: str | None, zone: tzinfo
) -> tuple[datetime | None, datetime | None]:
    since = _bound(start_date, zone, end=False) if start_date else None
    until = _bound(end_date, zone, end=True) if end_date else None
    if since and until and since >= until:
        raise fail("invalid_range", "start_date must be before end_date")
    return since, until


def paged_meta(
    count: int, total: int | None, offset: int, has_more: bool | None = None
) -> dict[str, Any]:
    if has_more is None:
        has_more = total is not None and offset + count < total
    return {
        "count": count,
        "total": total,
        "has_more": has_more,
        "next_offset": offset + count if has_more else None,
    }


def run_metrics(result: RunDigest) -> RunMetrics:
    fields = result.model_dump(exclude={"run_id", "cost_usd", "tool_call_groups", "cost_by_model"})
    by_model = [
        ModelCostRow(**m.model_dump(exclude={"cost_usd"}), cost=Money(value=m.cost_usd))
        for m in result.cost_by_model
    ]
    return RunMetrics(**fields, cost=usd(result.cost_usd), cost_by_model=by_model)


def turn_row(turn: Turn) -> TurnRow:
    return TurnRow(**turn.model_dump(exclude={"cost_usd"}), cost=usd(turn.cost_usd))


def run_row(run: runs_api.BrowserRun) -> RunRow:
    return RunRow(
        id=run.run_id,
        title=run.title,
        created_at=run.created_at,
        user_id=run.user_id,
        org_id=run.org_id,
        source=run.source,
        status=run.status,
        experiment_id=run.experiment_id,
        experiment_number=run.experiment_number,
        case_id=run.case_id,
    )


def stats_row(stats: GroupStats) -> StatsRow:
    return StatsRow(**stats.model_dump(exclude={"cost_usd"}), cost=Money(value=stats.cost_usd))


def clip_value(value: Any, max_chars: int) -> Any:
    return value if len(repr(value)) <= max_chars else clip(str(value), max_chars)


def clipped_step(step: Step, max_chars: int) -> dict:
    out = step.model_dump(exclude_none=True)
    if step.message is not None:
        out["message"] = clip(content_text(step.message), max_chars)
    if step.reasoning_content is not None:
        out["reasoning_content"] = clip(step.reasoning_content, max_chars)
    for call in out.get("tool_calls") or []:
        call["arguments"] = clip_value(call["arguments"], max_chars)
    if step.observation is not None:
        results = out["observation"]["results"]
        for result, raw in zip(results, step.observation.results, strict=True):
            if raw.content is not None:
                result["content"] = clip(content_text(raw.content), max_chars)
    return out


Digested = tuple[RunDigest, list[Turn]]


class Runs:
    def __init__(self, state: State) -> None:
        self.state = state
        self.gate = asyncio.Semaphore(FETCH_CONCURRENCY_TOTAL)

    async def agent(self, ref: str) -> AgentDefinition:
        return await runs_api.agent_or_404(self.state.agents, check_agent_id(ref))

    async def cost_map(self) -> dict:
        return await asyncio.to_thread(load_cost_map, self.state.cfg.cost_map)

    async def trajectory(self, record: AgentDefinition, run_id: str) -> tuple[Trajectory, set]:
        async with self.state.session_factory() as session:
            trajectory = await runs_api.fetch_trajectory(
                record, run_id, self.state.runner_deps, self.state.sessions, session
            )
        reported = priced_steps(trajectory)
        backfill_step_costs(trajectory, await self.cost_map())
        return trajectory, reported

    async def digests(
        self, record: AgentDefinition, run_ids: list[str], budget: float
    ) -> list[Digested | HTTPException]:
        # One stored-run lookup and one cost map per call, and no DB connection
        # held across harness calls; whatever is not read within the budget
        # comes back as a per-run failure rather than outliving the request.
        async with self.state.session_factory() as session:
            keys = await trajectory_keys(session, record.id, run_ids)
        cost_map = await self.cost_map()
        deps = self.state.runner_deps
        client = (
            await runs_api.harness_client(deps, record)
            if runs_api.server_may_drive(record)
            else None
        )

        call_gate = asyncio.Semaphore(FETCH_CONCURRENCY)

        async def one(run_id: str) -> Digested:
            async with call_gate, self.gate:
                trajectory = await runs_api.read_trajectory(
                    record, run_id, deps, self.state.sessions, keys.get(run_id), client
                )
            reported = priced_steps(trajectory)
            backfill_step_costs(trajectory, cost_map)
            return digest(run_id, trajectory, reported), turns(trajectory)

        tasks = [asyncio.create_task(one(run_id)) for run_id in run_ids]
        try:
            remaining = budget - asyncio.get_running_loop().time()
            if tasks and remaining > 0:
                await asyncio.wait(tasks, timeout=remaining)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if client is not None:
                await client.aclose()
        return [outcome(task) for task in tasks]

    async def page(
        self, record: AgentDefinition, *, limit: int, offset: int, budget: float, **filters: Any
    ) -> runs_api.RunPage:
        try:
            async with asyncio.timeout_at(budget), self.state.session_factory() as session:
                return await runs_api.resolve_runs(
                    record,
                    self.state.runner_deps,
                    self.state.spans,
                    session,
                    limit=limit,
                    offset=offset,
                    **filters,
                )
        except TimeoutError as exc:
            raise fail(
                "harness_timeout",
                f"listing the agent's runs took longer than {CALL_BUDGET_SECONDS}s; "
                "narrow the window or the filters",
            ) from exc


def outcome(task: asyncio.Task[Digested]) -> Digested | HTTPException:
    if task.cancelled():
        return HTTPException(status_code=504, detail=OVER_BUDGET)
    exc = task.exception()
    if exc is None:
        return task.result()
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, httpx.HTTPError | TimeoutError):
        return HTTPException(status_code=502, detail=f"harness unreachable ({type(exc).__name__})")
    if isinstance(exc, ValueError):
        return HTTPException(status_code=502, detail="malformed trajectory")
    if isinstance(exc, SessionTooLarge):
        return HTTPException(status_code=413, detail="session too large to convert")
    if not isinstance(exc, Exception):
        raise exc
    LOGGER.error("mcp.trajectory_read_failed", exc_info=exc)
    return HTTPException(
        status_code=502, detail=f"trajectory could not be read ({type(exc).__name__})"
    )


# A harness answers an id it does not know with 404, or 400/422 when the id is not
# even of its shape: either way the run does not exist there.
NOT_FOUND_STATUSES = frozenset({400, 404, 422})
ROW_ERROR_CODES = {504: "harness_timeout", 413: "session_too_large"}


def unread(result: Digested | HTTPException) -> bool:
    return isinstance(result, HTTPException) and result.status_code not in NOT_FOUND_STATUSES


def unread_note(results: list[Digested | HTTPException], truncated: bool) -> Note | None:
    unread_count = sum(1 for r in results if unread(r))
    parts = []
    if truncated:
        parts.append("the window holds more runs than were read")
    if unread_count:
        parts.append(f"{unread_count} runs could not be read (time budget or harness errors)")
    return Note(code="partial_coverage", message="; ".join(parts)) if parts else None


def with_digest(row: RunRow, result: Digested | HTTPException, include: set[str]) -> RunRow:
    if isinstance(result, HTTPException):
        if result.status_code in NOT_FOUND_STATUSES:
            row.note = Note(code="not_found")
        else:
            row.error = ROW_ERROR_CODES.get(result.status_code, "harness_failed")
            row.detail = f"trajectory: {result.detail}"
        return row
    found, run_turns = result
    if "metrics" in include:
        row.metrics = run_metrics(found)
    if "turns" in include:
        row.turns = [turn_row(t) for t in run_turns]
    if "tool_calls" in include:
        row.tool_calls = [ToolCallRow(**g.model_dump()) for g in found.tool_call_groups]
    return row


def harness_note(page: runs_api.RunPage) -> Note | None:
    if not page.harness_note:
        return None
    return Note(code="partial_coverage", message=page.harness_note)


def register(mcp: FastMCP, state: State) -> None:
    runs = Runs(state)

    @mcp.tool(description=d.FIND_AGENTS, **READ_TOOL)
    @expected_errors
    async def find_agents(
        workspace: str | None = None, include_archived: bool = False
    ) -> FindAgentsResponse:
        found = await state.agents.list_agents(
            workspace=workspace, include_archived=include_archived
        )
        rows = []
        for agent in found:
            view = agent_view(agent)
            rows.append(
                AgentRow(
                    id=view["ref"],
                    name=view["name"],
                    workspace=view["workspace"],
                    workspace_type=view["workspace_type"],
                    trajectory_source=view["trajectory_source"],
                    driver=view["driver"],
                    archived=view["archived"],
                )
            )
        return FindAgentsResponse(data=rows, meta=PlainMeta(count=len(rows)))

    @mcp.tool(description=d.FIND_RUNS, **HARNESS_READ_TOOL)
    @expected_errors
    async def find_runs(
        agent: str,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str = "all",
        query: str | None = None,
        user_id: str | None = None,
        org_id: str | None = None,
        timezone: str = "UTC",
        tool_error: str | None = None,
        failed_only: bool = False,
        include: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> FindRunsResponse:
        wanted = check_include(include or [], RUN_INCLUDES)
        check_choice("invalid_source", source, RUN_SOURCES)
        by_digest = tool_error is not None or failed_only
        if by_digest:
            wanted = wanted | {"metrics"}
        if wanted and limit > FIND_RUNS_MAX:
            raise fail(
                "invalid_limit",
                f"at most {FIND_RUNS_MAX} with include, tool_error or failed_only; got {limit}. "
                "List the window without them (up to 5000), then get_runs for the ids in batches",
            )
        check_page(limit, offset, FIND_RUNS_MAX if wanted else FIND_RUNS_LIST_MAX)
        since, until = window(start_date, end_date, zone_of(timezone))
        budget = deadline()
        record = await runs.agent(agent)
        filters = {
            "source": source,
            "since": since,
            "until": until,
            "query": query,
            "user_id": user_id,
            "org_id": org_id,
        }
        if by_digest:
            # Which runs failed, or errored in a tool, is known only from their
            # trajectories: digest the window (as get_run_stats does), keep the
            # matching runs, then page those.
            def matches(found: RunDigest) -> bool:
                if failed_only and not found.failures:
                    return False
                return tool_error is None or any(
                    e.tool == tool_error for e in found.tool_errors_by_tool
                )

            listing = await runs.page(
                record, limit=STATS_MAX_RUNS, offset=0, budget=budget, **filters
            )
            results = await runs.digests(record, [run.run_id for run in listing.runs], budget)
            # A run that could not be read might match: it stays, carrying its
            # note, rather than vanishing from the count.
            hits = [
                (run, result)
                for run, result in zip(listing.runs, results, strict=True)
                if (
                    result.status_code not in NOT_FOUND_STATUSES
                    if isinstance(result, HTTPException)
                    else matches(result[0])
                )
            ]
            rows = [
                with_digest(run_row(run), result, wanted)
                for run, result in hits[offset : offset + limit]
            ]
            note = harness_note(listing) or unread_note(
                results, truncated=listing.total > len(listing.runs)
            )
            meta = PagedMeta(**paged_meta(len(rows), len(hits), offset), note=note)
            return FindRunsResponse(data=rows, meta=meta)
        page = await runs.page(record, limit=limit, offset=offset, budget=budget, **filters)
        rows = [run_row(run) for run in page.runs]
        note = harness_note(page)
        if wanted:
            results = await runs.digests(record, [run.run_id for run in page.runs], budget)
            rows = [with_digest(r, res, wanted) for r, res in zip(rows, results, strict=True)]
            note = note or unread_note(results, truncated=False)
        meta = PagedMeta(**paged_meta(len(rows), page.total, offset), note=note)
        return FindRunsResponse(data=rows, meta=meta)

    @mcp.tool(description=d.GET_RUNS, **HARNESS_READ_TOOL)
    @expected_errors
    async def get_runs(
        agent: str, run_ids: list[str], include: list[str] | None = None
    ) -> GetRunsResponse:
        check_ids(run_ids, GET_RUNS_MAX)
        check_run_ids(run_ids)
        wanted = check_include(["metrics"] if include is None else include, RUN_INCLUDES)
        budget = deadline()
        record = await runs.agent(agent)
        page = await runs.page(record, limit=len(run_ids), offset=0, budget=budget, run_ids=run_ids)
        listed = {run.run_id: run for run in page.runs}
        results = await runs.digests(record, run_ids, budget)
        rows = []
        for run_id, result in zip(run_ids, results, strict=True):
            run = listed.get(run_id)
            row = run_row(run) if run else RunRow(id=run_id)
            rows.append(with_digest(row, result, wanted))
        unmatched = sum(1 for row in rows if row.note and row.note.code == "not_found")
        failed = sum(1 for row in rows if row.error)
        meta = BatchMeta(
            count=len(rows),
            matched=len(rows) - unmatched - failed,
            unmatched=unmatched,
            failed=failed or None,
        )
        return GetRunsResponse(data=rows, meta=meta)

    @mcp.tool(description=d.GET_RUN_STATS, **HARNESS_READ_TOOL)
    @expected_errors
    async def get_run_stats(
        agent: str,
        start_date: str | None = None,
        end_date: str | None = None,
        group_by: str | list[str] = "none",
        timezone: str = "UTC",
        source: str = "all",
        query: str | None = None,
        user_id: str | None = None,
        org_id: str | None = None,
        max_runs: int = 500,
    ) -> GetRunStatsResponse:
        groupings = [group_by] if isinstance(group_by, str) else list(dict.fromkeys(group_by))
        bad = [g for g in groupings if g not in GROUP_BY_VALUES]
        if bad or not groupings:
            raise fail("invalid_group_by", f"{bad or group_by!r}; valid: {list(GROUP_BY_VALUES)}")
        check_choice("invalid_source", source, RUN_SOURCES)
        if not 1 <= max_runs <= STATS_MAX_RUNS:
            raise fail(
                "invalid_limit", f"max_runs must be between 1 and {STATS_MAX_RUNS}; got {max_runs}"
            )
        zone = zone_of(timezone)
        since, until = window(start_date, end_date, zone)
        budget = deadline()
        record = await runs.agent(agent)
        filters = {
            "source": source,
            "since": since,
            "until": until,
            "query": query,
            "user_id": user_id,
            "org_id": org_id,
        }
        page = await runs.page(record, limit=max_runs, offset=0, budget=budget, **filters)
        listed, in_window = page.runs, page.total
        results = await runs.digests(record, [run.run_id for run in listed], budget)
        note = harness_note(page) or unread_note(results, truncated=False)
        items = [
            StatsInput(
                digest=result[0],
                turn_durations=[
                    t.duration_seconds for t in result[1] if t.duration_seconds is not None
                ],
                source=run.source,
                user_id=run.user_id,
                created_at=run.created_at,
            )
            for run, result in zip(listed, results, strict=True)
            if not isinstance(result, HTTPException)
        ]
        rows = [
            stats_row(s)
            for g in groupings
            for s in (group_stats(items, zone, g) if items else [])  # type: ignore[arg-type]
        ]
        meta = StatsMeta(
            count=len(rows),
            group_by=groupings,
            timezone=timezone,
            runs_considered=len(listed),
            runs_in_window=in_window,
            capped=in_window > len(listed),
            unavailable=len(listed) - len(items),
            note=note,
        )
        return GetRunStatsResponse(data=rows, meta=meta)

    @mcp.tool(description=d.GET_TRAJECTORY, **HARNESS_READ_TOOL)
    @expected_errors
    async def get_trajectory(
        agent: str,
        run_id: str,
        subagent: int | None = None,
        max_chars: int = 2000,
        limit: int = 20,
        offset: int = 0,
    ) -> GetTrajectoryResponse:
        check_page(limit, offset, STEP_PAGE_MAX)
        if not 100 <= max_chars <= 50000:
            raise fail("invalid_max_chars", "max_chars must be between 100 and 50000")
        check_run_ids([run_id])
        record = await runs.agent(agent)
        root, _ = await runs.trajectory(record, run_id)
        subs = root.subagent_trajectories or []
        trajectory = root
        if subagent is not None:
            if not 0 <= subagent < len(subs):
                raise fail("not_found", f"run has {len(subs)} subagent trajectories")
            trajectory = subs[subagent]
        steps = [clipped_step(s, max_chars) for s in trajectory.steps[offset : offset + limit]]
        meta = TrajectoryMeta(
            **paged_meta(len(steps), len(trajectory.steps), offset),
            schema_version=root.schema_version,
            session_id=trajectory.session_id,
            trajectory_id=trajectory.trajectory_id,
            agent=trajectory.agent.model_dump(exclude_none=True, exclude={"tool_definitions"})
            if trajectory.agent
            else None,
            final_metrics=trajectory.final_metrics.model_dump(exclude_none=True)
            if trajectory.final_metrics
            else None,
            subagents=[
                SubagentRef(
                    index=i,
                    trajectory_id=sub.trajectory_id,
                    agent=sub.agent.name if sub.agent else None,
                    steps=len(sub.steps),
                )
                for i, sub in enumerate(subs)
            ],
        )
        return GetTrajectoryResponse(data=steps, meta=meta)

    @mcp.tool(description=d.GET_RUN_FILES, **HARNESS_READ_TOOL)
    @expected_errors
    async def get_run_files(agent: str, run_id: str) -> GetRunFilesResponse:
        check_run_ids([run_id])
        listing = await runs_api.list_files(
            check_agent_id(agent), run_id, state.runner_deps, state.agents
        )
        rows = [FileRow(**f) for f in listing["files"]]
        return GetRunFilesResponse(data=rows, meta=PlainMeta(count=len(rows)))

    @mcp.tool(description=d.DOWNLOAD_RUN_FILE, **HARNESS_READ_TOOL)
    @expected_errors
    async def download_run_file(
        agent: str, run_id: str, path: str, max_chars: int = FILE_CHARS, offset: int = 0
    ) -> DownloadRunFileResponse:
        if not 1 <= max_chars <= FILE_CHARS_MAX:
            raise fail("invalid_max_chars", f"max_chars must be between 1 and {FILE_CHARS_MAX}")
        if offset < 0:
            raise fail("invalid_offset", f"offset must be 0 or greater; got {offset}")
        check_run_ids([run_id])
        response = await runs_api.get_file(
            check_agent_id(agent), run_id, path, state.runner_deps, state.agents
        )
        body = b"".join(
            [c.encode() if isinstance(c, str) else bytes(c) async for c in response.body_iterator]
        )
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            row = FileText(
                path=path,
                size=len(body),
                note=Note(code="not_applicable", message="binary file; not returned as text"),
            )
            meta = PagedMeta(count=1, total=None, has_more=False)
            return DownloadRunFileResponse(data=[row], meta=meta)
        chunk = text[offset : offset + max_chars]
        window_meta = paged_meta(len(chunk), len(text), offset)
        meta = PagedMeta(**{**window_meta, "count": 1})
        return DownloadRunFileResponse(
            data=[FileText(path=path, size=len(body), text=chunk)], meta=meta
        )

    @mcp.tool(description=d.FIND_EXPERIMENTS, **READ_TOOL)
    @expected_errors
    async def find_experiments(
        workspace: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> FindExperimentsResponse:
        check_page(limit, offset, EXPERIMENTS_MAX)
        if status is not None:
            check_choice("invalid_status", status, tuple(ExperimentStatus))
        async with state.session_factory() as session:
            found = await experiments_api.list_experiments(
                session,
                workspace=workspace,
                status=ExperimentStatus(status) if status else None,
                limit=limit + 1,
                offset=offset,
            )
        rows = [ExperimentRow(**{k: v for k, v in e.items() if k != "config"}) for e in found]
        meta = PagedMeta(**paged_meta(min(len(rows), limit), None, offset, len(rows) > limit))
        rows = rows[:limit]
        return FindExperimentsResponse(data=rows, meta=meta)

    @mcp.tool(description=d.GET_EXPERIMENTS, **READ_TOOL)
    @expected_errors
    async def get_experiments(
        experiment_ids: list[int], include: list[str] | None = None
    ) -> GetExperimentsResponse:
        check_ids(experiment_ids, GET_EXPERIMENTS_MAX)
        wanted = check_include(include or [], EXPERIMENT_INCLUDES)
        rows = []
        async with state.session_factory() as session:
            for experiment_id in experiment_ids:
                try:
                    exp = await db_experiments.get_experiment(session, experiment_id)
                except UnknownExperiment:
                    rows.append(ExperimentRow(id=experiment_id, note=Note(code="not_found")))
                    continue
                extra: dict[str, Any] = {
                    "progress": (await db_scorecard.progress(session, experiment_id)).model_dump()
                }
                if "scorecard" in wanted:
                    extra["scorecard"] = await experiments_api.scorecard(
                        experiment_id, state.store, None
                    )
                rows.append(ExperimentRow(**exp.model_dump(exclude={"config"}), **extra))
        unmatched = sum(1 for r in rows if r.note)
        meta = BatchMeta(count=len(rows), matched=len(rows) - unmatched, unmatched=unmatched)
        return GetExperimentsResponse(data=rows, meta=meta)

    @mcp.tool(description=d.GET_EXPERIMENT_RUNS, **READ_TOOL)
    @expected_errors
    async def get_experiment_runs(
        experiment_id: int, failed_only: bool = False, limit: int = 50, offset: int = 0
    ) -> GetExperimentRunsResponse:
        check_page(limit, offset, FIND_RUNS_MAX)
        exp = await state.store.get_experiment(experiment_id)
        rows = []
        for run in await state.store.run_details(experiment_id):
            evaluations = [
                Evaluation(
                    name=e.name,
                    kind=e.kind,
                    value=e.value,
                    label=e.label,
                    explanation=clip(e.explanation, 1000),
                    error=e.error,
                )
                for e in run.evaluations
            ]
            failed = run.agent_run.error is not None or any(
                e.kind == "score" and e.value is not None and e.value < 1 for e in evaluations
            )
            if failed_only and not failed:
                continue
            rows.append(
                ExperimentRunRow(
                    id=run.agent_run.harness_run_id,
                    case_id=run.case_id,
                    repetition=run.repetition,
                    status=run.agent_run.status,
                    error=run.agent_run.error,
                    answer=clip(run.agent_run.answer, 2000),
                    started_at=run.agent_run.started_at,
                    ended_at=run.agent_run.ended_at,
                    metrics=run.agent_run.metrics,
                    evaluations=evaluations,
                )
            )
        page = rows[offset : offset + limit]
        meta = ExperimentRunsMeta(**paged_meta(len(page), len(rows), offset), agent=exp.agent)
        return GetExperimentRunsResponse(data=page, meta=meta)
