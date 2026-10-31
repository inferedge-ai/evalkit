from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from evalkit.contracts.enums import EvaluatorKind
from evalkit.contracts.harness import RunListItem, RunListParams
from evalkit.core.harness import HarnessClient
from evalkit.store.base import AgentRunRecord, EvaluationRow, RunEvalContext

UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
MAX_PULL = 100
HARNESS_PAGE = 200
MAX_HARNESS_SCAN = 10000

Exclude = Callable[[list[str]], Awaitable[set[str]]]


class HarnessRuns(BaseModel):
    runs: list[RunListItem]
    total: int
    capped: bool = False


SCAN_NOTE = (
    f"stopped after scanning {MAX_HARNESS_SCAN} harness runs; narrow the window with since/until"
)


class RunBundle(BaseModel):
    run_id: str
    source: Literal["app", "eval"]
    status: str | None = None
    created_at: datetime | None = None
    title: str | None = None
    run: dict
    trajectory: dict | None = None
    eval: RunEvalContext | None = None
    pull_error: str | None = None


def in_window(run: RunListItem, since: datetime | None, until: datetime | None) -> bool:
    if run.created_at is None:
        return True
    return (since is None or run.created_at >= since) and (until is None or run.created_at < until)


async def list_harness_runs(
    harness: HarnessClient,
    *,
    wanted: int,
    since: datetime | None = None,
    until: datetime | None = None,
    query: str | None = None,
    user_id: str | None = None,
    org_id: str | None = None,
    exclude: Exclude | None = None,
) -> HarnessRuns:
    kept: list[RunListItem] = []
    sessions: set[str] = set()
    dropped = offset = 0
    size = HARNESS_PAGE

    def new_session(run: RunListItem) -> bool:
        if run.session_id is None:
            return True
        if run.session_id in sessions:
            return False
        sessions.add(run.session_id)
        return True

    while True:
        page = await harness.list_runs(
            RunListParams(
                query=query,
                user_id=user_id,
                org_id=org_id,
                limit=size,
                offset=offset,
                since=since,
                until=until,
            )
        )
        window = [run for run in page.runs if in_window(run, since, until)]
        excluded = await exclude([run.run_id for run in window]) if exclude and window else set()
        fresh = [run for run in window if run.run_id not in excluded and new_session(run)]
        kept += fresh
        dropped += len(page.runs) - len(fresh)
        offset += len(page.runs)
        oldest = page.runs[-1].created_at if page.runs else None
        # A short page is the end only when the harness says so; a harness that
        # caps its page below `size` keeps being paged at its own size.
        size = min(size, len(page.runs)) or size
        if (
            not page.runs
            or offset >= page.total
            or (since is not None and oldest is not None and oldest < since)
        ):
            return HarnessRuns(runs=kept, total=len(kept))
        if len(kept) >= wanted:
            return HarnessRuns(runs=kept, total=max(page.total - dropped, len(kept)))
        if offset >= MAX_HARNESS_SCAN:
            return HarnessRuns(runs=kept, total=max(page.total - dropped, len(kept)), capped=True)


def safe_dir(run_id: str) -> str:
    return UNSAFE.sub("_", run_id)


def run_body(
    run_id: str,
    source: Literal["app", "eval"],
    record: AgentRunRecord | None,
    eval_context: RunEvalContext | None,
) -> dict:
    body: dict = {"run_id": run_id, "source": source}
    if eval_context is not None:
        body["input"] = eval_context.case.input
    if record is not None:
        body |= {
            "status": record.status.value,
            "answer": record.answer,
            "output": record.output,
            "error": record.error,
            "started_at": record.started_at.isoformat(),
            "ended_at": record.ended_at.isoformat() if record.ended_at else None,
            "metrics": record.metrics,
        }
    return body


def scores(evaluations: list[EvaluationRow]) -> dict[str, float]:
    return {
        row.name: row.value
        for row in evaluations
        if row.kind is EvaluatorKind.SCORE and row.error is None and row.value is not None
    }


def index_row(bundle: RunBundle) -> dict:
    context = bundle.eval
    return {
        "run_id": bundle.run_id,
        "dir": f"runs/{safe_dir(bundle.run_id)}",
        "source": bundle.source,
        "status": bundle.status,
        "created_at": bundle.created_at.isoformat() if bundle.created_at else None,
        "title": bundle.title,
        "experiment_id": context.experiment_id if context else None,
        "case_id": context.case.case_id if context else None,
        "repetition": context.repetition if context else None,
        "scores": scores(context.evaluations) if context else None,
        "error": bundle.run.get("error"),
        "pull_error": bundle.pull_error,
    }


def _json(doc: object) -> bytes:
    return json.dumps(doc, indent=2, ensure_ascii=False, default=str).encode()


def bundle_files(bundle: RunBundle) -> dict[str, bytes]:
    base = f"runs/{safe_dir(bundle.run_id)}"
    run = (
        bundle.run if bundle.pull_error is None else {**bundle.run, "pull_error": bundle.pull_error}
    )
    files = {f"{base}/run.json": _json(run)}
    if bundle.trajectory is not None:
        files[f"{base}/trajectory.json"] = _json(bundle.trajectory)
    if bundle.eval is not None:
        case = bundle.eval.case
        files[f"{base}/case.json"] = _json(
            {
                "case_id": case.case_id,
                "content_hash": case.content_hash,
                "input": case.input,
                "expected": case.expected,
                "evaluators": case.effective_evaluators,
                "tags": case.tags,
            }
        )
        files[f"{base}/evaluations.json"] = _json(
            [row.model_dump(mode="json", exclude_none=True) for row in bundle.eval.evaluations]
        )
    return files


def merge_index(existing: dict | None, fresh: dict) -> dict:
    if existing is None:
        return fresh
    replaced = {row["run_id"] for row in fresh["runs"]}
    kept = [row for row in existing["runs"] if row["run_id"] not in replaced]
    return {**fresh, "runs": kept + fresh["runs"]}
