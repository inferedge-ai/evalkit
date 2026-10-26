from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.contracts.cases import Case, content_hash
from evalkit.contracts.enums import DatasetKind, SyncSource
from evalkit.core.access import WorkspaceRef
from evalkit.db.cases import sync_dataset
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from evalkit.store.base import CaseFile, IncomingCase, IncomingOverlay, SyncResult
from tests.identity import seed_identity


@dataclass
class World:
    admin_id: int
    akshat_id: int
    canonical: WorkspaceRef
    dev: WorkspaceRef
    prod: WorkspaceRef
    akshat_ws: WorkspaceRef


async def seed_world(session_factory: async_sessionmaker[AsyncSession]) -> World:
    await seed_identity(
        session_factory,
        {"admin": "password", "akshat": "password"},
        shared=("canonical", "dev", "prod"),
    )
    ids = {user.username: user.id for user in await UserStore(session_factory).list()}
    workspaces = WorkspaceStore(session_factory)
    return World(
        admin_id=ids["admin"],
        akshat_id=ids["akshat"],
        canonical=await workspaces.get_by_name("canonical"),
        dev=await workspaces.get_by_name("dev"),
        prod=await workspaces.get_by_name("prod"),
        akshat_ws=await workspaces.personal_of("akshat"),
    )


def incoming(
    case_id: str,
    question: str = "What is EBITDA?",
    tags: tuple[str, ...] = (),
    files: tuple[CaseFile, ...] = (),
) -> IncomingCase:
    case = Case(id=case_id, input={"question": question}, tags=list(tags))
    return IncomingCase(
        case_id=case_id,
        content_hash=content_hash(case, [f.model_dump() for f in files]),
        input=case.input,
        evaluators=case.evaluators.as_metadata(),
        tags=case.tags,
        files=list(files),
    )


def overlay(target: str, agents: list[str], tags: tuple[str, ...] = ()) -> IncomingOverlay:
    return IncomingOverlay(
        target_case_id=target,
        agents=agents,
        tags=list(tags),
        evaluators={"custom": {"tone": {"strict": True}}},
        source_file="overlays.yaml",
    )


async def sync(
    session: AsyncSession,
    workspace: WorkspaceRef,
    actor_id: int,
    cases: list[IncomingCase],
    *,
    dataset: str = "finance",
    overlays: tuple[IncomingOverlay, ...] = (),
    full: bool = True,
    detail: dict | None = None,
) -> SyncResult:
    return await sync_dataset(
        session,
        workspace=workspace,
        dataset=dataset,
        kind=DatasetKind.SUITE,
        cases=cases,
        overlays=list(overlays),
        full=full,
        actor_id=actor_id,
        source=SyncSource.GIT,
        detail=detail or {},
    )


async def count(session_factory: async_sessionmaker[AsyncSession], model: type) -> int:
    async with session_factory() as s:
        found = await s.scalar(select(func.count()).select_from(model))
    assert found is not None
    return found
