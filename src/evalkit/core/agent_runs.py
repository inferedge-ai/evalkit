from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from evalkit.contracts.atif import Trajectory
from evalkit.contracts.enums import RunStatus, TrajectoryStatus
from evalkit.core.blob import BlobStore, trajectory_key
from evalkit.core.trajectory import TrajectoryView, backfill_step_costs, derive_metrics
from evalkit.store.base import AgentRunRecord


async def store_trajectory(
    blob_store: BlobStore,
    cost_map: dict[str, Any],
    agent_id: int,
    harness_run_id: str,
    trajectory: Trajectory,
    status: TrajectoryStatus | None,
    model: str | None,
) -> str:
    backfill_step_costs(trajectory, cost_map, model)
    if status == TrajectoryStatus.PARTIAL:
        trajectory.extra = {**(trajectory.extra or {}), "trajectory_status": "partial"}
    key = trajectory_key(agent_id, harness_run_id)
    await blob_store.put_object(key, json.dumps(trajectory.model_dump(exclude_none=True)).encode())
    return key


async def build_agent_run(
    blob_store: BlobStore,
    cost_map: dict[str, Any],
    agent_id: int,
    *,
    harness_run_id: str,
    status: RunStatus,
    started_at: datetime,
    ended_at: datetime | None,
    trajectory: Trajectory | None,
    trajectory_status: TrajectoryStatus | None,
    trajectory_note: str | None = None,
    answer: str | None = None,
    output: dict[str, Any] | None = None,
    error: str | None = None,
    metrics: dict[str, Any] | None = None,
    wall_ms: int | None = None,
    title: str | None = None,
    model: str | None = None,
) -> AgentRunRecord:
    run_metrics = dict(metrics or {})
    key: str | None = None
    if trajectory is not None:
        key = await store_trajectory(
            blob_store, cost_map, agent_id, harness_run_id, trajectory, trajectory_status, model
        )
        derived = derive_metrics(TrajectoryView(trajectory), wall_ms)
        run_metrics.update(derived.model_dump(exclude_none=True))
    if wall_ms is not None:
        run_metrics.setdefault("duration_ms", wall_ms)
    return AgentRunRecord(
        harness_run_id=harness_run_id,
        status=status,
        answer=answer,
        output=output,
        error=error,
        started_at=started_at,
        ended_at=ended_at,
        trajectory_key=key,
        trajectory_status=trajectory_status,
        trajectory_note=trajectory_note,
        metrics=run_metrics,
        title=title,
    )
