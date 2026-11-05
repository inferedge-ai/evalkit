from __future__ import annotations

import os
import socket

from evalkit.client.api import AsyncApi, encode
from evalkit.contracts.enums import ExperimentStatus
from evalkit.core.runner import LEASE_SECONDS, ExperimentRequest
from evalkit.store.base import (
    AgentRunRecord,
    CaseRep,
    CaseView,
    EvaluationRow,
    Experiment,
    RunDetail,
    RunForScoring,
    RunRef,
    Selection,
)

RENEW_TIMEOUT_SECONDS = LEASE_SECONDS / 6


def local_holder(username: str) -> str:
    return f"local:{username}@{socket.gethostname()}:{os.getpid()}"


class HttpStore:
    def __init__(self, api: AsyncApi):
        self._api = api

    async def aclose(self) -> None:
        await self._api.aclose()

    async def cases_for(
        self, dataset: str, selection: Selection, *, agent: str | None
    ) -> list[CaseView]:
        rows = (
            await self._api.post(
                f"/api/datasets/{encode(dataset)}/cases/select",
                json={
                    "selection": selection.model_dump(exclude_none=True),
                    "agent": agent,
                },
                retry=True,
            )
        ).json()
        return [CaseView.model_validate(r) for r in rows]

    async def start_experiment(self, req: ExperimentRequest) -> tuple[Experiment, list[str]]:
        body = req.model_dump(mode="json", exclude_none=True)
        created = (await self._api.post("/api/experiments", json=body)).json()
        return Experiment.model_validate(created), list(created.get("dropped_cases") or [])

    async def get_experiment(self, exp_id: int) -> Experiment:
        return Experiment.model_validate((await self._api.get(f"/api/experiments/{exp_id}")).json())

    async def pinned_cases(self, exp_id: int) -> list[CaseView]:
        rows = (await self._api.get(f"/api/experiments/{exp_id}/cases")).json()
        return [CaseView.model_validate(r) for r in rows]

    async def transition(
        self, exp_id: int, to: ExperimentStatus, holder: str | None, error: str | None = None
    ) -> Experiment:
        body = {"status": to.value, "holder": holder, "error": error}
        resp = await self._api.patch(f"/api/experiments/{exp_id}", json=body, retry=True)
        return Experiment.model_validate(resp.json())

    async def renew_lease(self, exp_id: int, holder: str) -> bool:
        resp = await self._api.post(
            f"/api/experiments/{exp_id}/lease",
            json={"holder": holder},
            retry=True,
            timeout=RENEW_TIMEOUT_SECONDS,
        )
        return bool(resp.json()["renewed"])

    async def set_model(self, exp_id: int, model: str) -> None:
        await self._api.patch(f"/api/experiments/{exp_id}", json={"model": model}, retry=True)

    async def pending_runs(self, exp_id: int) -> list[CaseRep]:
        rows = (await self._api.get(f"/api/experiments/{exp_id}/pending")).json()
        return [CaseRep.model_validate(r) for r in rows]

    async def harness_run_id_used(self, exp_id: int, harness_run_id: str) -> str | None:
        resp = await self._api.get(
            f"/api/experiments/{exp_id}/harness-run-id", params={"value": harness_run_id}
        )
        return resp.json()["used"]

    async def record_run(
        self,
        exp_id: int,
        case_id: str,
        repetition: int,
        run: AgentRunRecord,
        *,
        holder: str | None = None,
    ) -> RunRef:
        resp = await self._api.post(
            f"/api/experiments/{exp_id}/runs",
            json={
                "case_id": case_id,
                "repetition": repetition,
                "run": run.model_dump(mode="json"),
                "holder": holder,
            },
        )
        return RunRef.model_validate(resp.json())

    async def runs_to_score(self, exp_id: int, *, force: bool) -> list[RunForScoring]:
        rows = (
            await self._api.get(
                f"/api/experiments/{exp_id}/runs-to-score", params={"force": str(force).lower()}
            )
        ).json()
        return [RunForScoring.model_validate(r) for r in rows]

    async def publish_evaluations(
        self,
        experiment_run_id: int,
        rows: list[EvaluationRow],
        *,
        force: bool,
        holder: str | None = None,
    ) -> None:
        await self._api.post(
            f"/api/experiment-runs/{experiment_run_id}/evaluations",
            json={
                "rows": [r.model_dump(mode="json") for r in rows],
                "force": force,
                "holder": holder,
            },
            retry=True,
        )

    async def run_details(self, exp_id: int) -> list[RunDetail]:
        rows = (await self._api.get(f"/api/experiments/{exp_id}/runs")).json()
        return [RunDetail.model_validate(r) for r in rows]
