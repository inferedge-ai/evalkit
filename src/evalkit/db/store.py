from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.enums import DatasetKind, ExperimentStatus, SyncSource
from evalkit.core.access import WorkspaceRef
from evalkit.core.overlays import OverlayBlock
from evalkit.db import cases, experiments, fixture_blobs, models, runs, scorecard
from evalkit.db.cases import sync_dataset
from evalkit.store.base import (
    AgentRunRecord,
    AggregateRows,
    CaseRep,
    CaseView,
    DatasetRow,
    EvaluationRow,
    Experiment,
    IncomingCase,
    IncomingOverlay,
    NewExperiment,
    RunDetail,
    RunForScoring,
    RunRef,
    Selection,
    SyncResult,
    select_cases,
)


class PgStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def aclose(self) -> None:
        return None

    async def dataset(self, ref: str) -> DatasetRow:
        async with self._sf() as s:
            return await cases.dataset_by_ref(s, ref)

    async def cases_for(
        self, dataset: str, selection: Selection, *, agent: str | None
    ) -> list[CaseView]:
        async with self._sf() as s:
            row = await cases.dataset_by_ref(s, dataset)
            selected = select_cases(await cases.current_cases(s, row, agent=agent), selection)
        return selected

    async def create_experiment(self, req: NewExperiment) -> Experiment:
        async with self._sf() as s:
            exp = await experiments.create_experiment(s, req)
            await s.commit()
            return exp

    async def get_experiment(self, exp_id: int) -> Experiment:
        async with self._sf() as s:
            return await experiments.get_experiment(s, exp_id)

    async def pinned_cases(self, exp_id: int) -> list[CaseView]:
        async with self._sf() as s:
            return await experiments.pinned_cases(s, exp_id)

    async def transition(
        self, exp_id: int, to: ExperimentStatus, holder: str | None, error: str | None = None
    ) -> Experiment:
        async with self._sf() as s:
            exp = await experiments.transition(s, exp_id, to, holder, error)
            await s.commit()
            return exp

    async def renew_lease(self, exp_id: int, holder: str) -> bool:
        async with self._sf() as s:
            renewed = await experiments.renew_lease(s, exp_id, holder)
            await s.commit()
            return renewed

    async def set_model(self, exp_id: int, model: str) -> None:
        async with self._sf() as s:
            await experiments.set_model(s, exp_id, model)
            await s.commit()

    async def pending_runs(self, exp_id: int) -> list[CaseRep]:
        async with self._sf() as s:
            return await runs.pending_runs(s, exp_id)

    async def harness_run_id_used(self, exp_id: int, harness_run_id: str) -> str | None:
        async with self._sf() as s:
            return await runs.harness_run_id_used(s, exp_id, harness_run_id)

    async def record_run(
        self,
        exp_id: int,
        case_id: str,
        repetition: int,
        run: AgentRunRecord,
        *,
        holder: str | None = None,
    ) -> RunRef:
        async with self._sf() as s:
            ref = await runs.record_run(s, exp_id, case_id, repetition, run, None, holder=holder)
            await s.commit()
            return ref

    async def runs_to_score(self, exp_id: int, *, force: bool) -> list[RunForScoring]:
        async with self._sf() as s:
            return await runs.runs_to_score(s, exp_id, force=force)

    async def publish_evaluations(
        self,
        experiment_run_id: int,
        rows: list[EvaluationRow],
        *,
        force: bool,
        holder: str | None = None,
    ) -> None:
        async with self._sf() as s:
            await runs.publish_evaluations(s, experiment_run_id, rows, force=force, holder=holder)
            await s.commit()

    async def aggregate(self, exp_id: int) -> AggregateRows:
        async with self._sf() as s:
            return await scorecard.aggregate(s, exp_id)

    async def run_details(self, exp_id: int) -> list[RunDetail]:
        async with self._sf() as s:
            return await runs.run_details(s, exp_id)

    async def _dataset_id(self, s: AsyncSession, workspace: WorkspaceRef, name: str) -> int | None:
        return await s.scalar(
            select(models.Dataset.id).where(
                models.Dataset.workspace_id == workspace.id, models.Dataset.name == name
            )
        )

    async def workspace_cases(
        self, workspace: WorkspaceRef, *, exclude_dataset: str | None
    ) -> dict[str, str]:
        async with self._sf() as s:
            excluded = (
                await self._dataset_id(s, workspace, exclude_dataset)
                if exclude_dataset is not None
                else None
            )
            return await cases.workspace_case_index(s, workspace.id, exclude_dataset=excluded)

    async def overlays_on(
        self, workspace: WorkspaceRef, case_ids: list[str], *, exclude_dataset: str
    ) -> dict[str, list[OverlayBlock]]:
        if not case_ids:
            return {}
        async with self._sf() as s:
            rows = await s.execute(
                select(models.Overlay, models.Dataset.name, models.Case.case_id)
                .join(models.Dataset, models.Dataset.id == models.Overlay.source_dataset_id)
                .join(models.Case, models.Case.id == models.Overlay.target_case)
                .where(
                    models.Dataset.workspace_id == workspace.id,
                    models.Dataset.name != exclude_dataset,
                    models.Case.case_id.in_(case_ids),
                )
            )
            out: dict[str, list[OverlayBlock]] = {}
            for overlay, source, case_id in rows.all():
                out.setdefault(case_id, []).append(
                    OverlayBlock(
                        source=source,
                        agents=list(overlay.agents),
                        tags=list(overlay.tags),
                        evaluators=overlay.evaluators,
                        source_file=overlay.source_file,
                    )
                )
            return out

    async def has_dataset(self, workspace: WorkspaceRef, name: str) -> bool:
        async with self._sf() as s:
            return await self._dataset_id(s, workspace, name) is not None

    async def known_blobs(self, shas: list[str]) -> set[str]:
        async with self._sf() as s:
            return await fixture_blobs.known_blobs(s, shas)

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
    ) -> SyncResult:
        async with self._sf() as s:
            result = await sync_dataset(
                s,
                workspace=workspace,
                dataset=dataset,
                kind=kind,
                cases=incoming,
                overlays=overlays,
                full=full,
                actor_id=actor_id,
                source=source,
                detail=detail,
            )
            await s.commit()
            return result
