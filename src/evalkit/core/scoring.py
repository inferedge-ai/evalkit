from __future__ import annotations

import json
from collections.abc import Collection, Iterable
from typing import Any

from evalkit.contracts.atif import validate_trajectory
from evalkit.contracts.enums import EvaluationSource, EvaluatorKind, ScoredOn, TrajectoryStatus
from evalkit.contracts.harness import Capabilities, answer_text
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.agents import AgentDefinition
from evalkit.core.blob import BlobStore, trajectory_key
from evalkit.core.evaluators.base import BUILTIN_GAUGES, EvalContext, execute
from evalkit.core.trajectory import RunMetrics, TrajectoryView, derive_metrics
from evalkit.store.base import AgentRunRecord, CaseView, EvaluationRow, ScoreRow

SCORABLE = (TrajectoryStatus.VALID, TrajectoryStatus.PARTIAL)
REFETCHABLE = (TrajectoryStatus.PARTIAL, TrajectoryStatus.UNAVAILABLE)


async def stored_trajectory_doc(
    blob_store: BlobStore, agent_id: int, run: AgentRunRecord
) -> tuple[dict | None, TrajectoryStatus | None]:
    status = run.trajectory_status
    if status in REFETCHABLE:
        candidate = trajectory_key(agent_id, run.harness_run_id)
        if await blob_store.object_exists(candidate):
            doc = json.loads(await blob_store.get_object(candidate))
            return doc, TrajectoryStatus((doc.get("extra") or {}).get("trajectory_status", "valid"))
    if run.trajectory_key and status in SCORABLE:
        return json.loads(await blob_store.get_object(run.trajectory_key)), status
    return None, status


async def load_view(
    blob_store: BlobStore, agent_id: int, run: AgentRunRecord
) -> TrajectoryView | None:
    doc, status = await stored_trajectory_doc(blob_store, agent_id, run)
    if doc is None or status not in SCORABLE:
        return None
    return TrajectoryView(validate_trajectory(doc))


def evaluation_rows(
    producer: str,
    source: EvaluationSource,
    scores: Iterable[ScoreRow],
    scored_on: ScoredOn = ScoredOn.LOCAL,
) -> list[EvaluationRow]:
    return [
        EvaluationRow(
            producer=producer,
            name=score.name,
            kind=EvaluatorKind.GAUGE if score.kind == "gauge" else EvaluatorKind.SCORE,
            source=source,
            scored_on=scored_on,
            value=score.score,
            label=score.label,
            explanation=score.explanation,
            error=score.error,
        )
        for score in scores
    ]


async def build_context(
    blob_store: BlobStore,
    agent: AgentDefinition,
    cost_map: dict[str, Any],
    record: AgentRunRecord,
    case: CaseView | None = None,
    judge: JudgeSettings | None = None,
    judge_api_key: str | None = None,
) -> tuple[EvalContext, TrajectoryView | None]:
    structured = record.output
    answer = (
        answer_text(record.answer, structured) if structured is not None else record.answer or ""
    )
    metrics_dict = dict(record.metrics)
    view = await load_view(blob_store, agent.id, record)
    if view is not None:
        wall = metrics_dict.get("duration_ms")
        metrics_dict = {**metrics_dict, **derive_metrics(view, wall).model_dump(exclude_none=True)}
    ctx = EvalContext(
        case=case,
        answer=answer,
        output=structured,
        trajectory_view=view,
        wall_ms=metrics_dict.get("duration_ms") or 0,
        judge_cfg=judge,
        judge_api_key=judge_api_key,
        metrics=RunMetrics.model_validate(metrics_dict) if metrics_dict else None,
        session_id=record.harness_run_id,
        cost_map=cost_map,
    )
    return ctx, view


async def run_gauges(
    ctx: EvalContext, caps: Capabilities, skip: Collection[str] = ()
) -> list[EvaluationRow]:
    rows: list[EvaluationRow] = []
    for name, gauge in BUILTIN_GAUGES.items():
        if name in skip:
            continue
        scores = await execute(gauge, ctx, {}, caps)
        rows.extend(evaluation_rows(name, EvaluationSource.BUILTIN, scores))
    return rows
