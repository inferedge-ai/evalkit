from dataclasses import dataclass, replace
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import State

from evalkit.api.auth import CurrentUser
from evalkit.api.deps import SessionDep
from evalkit.contracts.enums import WorkspaceRole
from evalkit.contracts.notes import PERSONAL_KEY_NOTE
from evalkit.core.access import Actor, WorkspaceRef, can_write
from evalkit.core.blob import BlobStore, TenantBlobStore
from evalkit.core.endpoints import EndpointRule, check_endpoint
from evalkit.core.runner import RunnerDeps
from evalkit.db.agents import AgentStore
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.keys import KeyLoader
from evalkit.db.llm_providers import LlmProviderStore
from evalkit.db.mappings import MappingStore
from evalkit.db.models import User, WorkspaceMember
from evalkit.db.review import ReviewLog
from evalkit.db.session import tenant_session
from evalkit.db.sessions import DbSessionReader
from evalkit.db.spans import SpanStore
from evalkit.db.store import PgStore
from evalkit.db.tokens import AgentTokens
from evalkit.db.trace_imports import TraceImportStore
from evalkit.db.trace_sources import TraceSourceStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore, load_ref, workspace_row


@dataclass(frozen=True)
class TenantStores:
    tenant_id: str
    session_factory: async_sessionmaker[AsyncSession]
    users: UserStore
    workspaces: WorkspaceStore
    agents: AgentStore
    evaluators: RemoteEvaluatorStore
    providers: LlmProviderStore
    sources: TraceSourceStore
    imports: TraceImportStore
    spans: SpanStore
    mappings: MappingStore
    tokens: AgentTokens
    keys: KeyLoader
    review: ReviewLog
    sessions: DbSessionReader
    store: PgStore
    blob: BlobStore
    deps: RunnerDeps

    def session(self):
        return tenant_session(self.session_factory, self.tenant_id)


def build_tenant_stores(state: State, tenant_id: str) -> TenantStores:
    sf = state.session_factory
    agents = AgentStore(sf, tenant_id)
    evaluators = RemoteEvaluatorStore(sf, tenant_id)
    spans = SpanStore(sf, tenant_id)
    mappings = MappingStore(sf, tenant_id)
    sessions = DbSessionReader(agents, spans, mappings, state.cfg.otel)
    store = PgStore(sf, tenant_id)
    keys = KeyLoader(sf, tenant_id, state.box)
    blob = TenantBlobStore(state.blob_store, tenant_id)
    template: RunnerDeps = state.runner_deps
    deps = replace(
        template,
        store=store,
        blob_store=blob,
        agents=agents,
        evaluators=evaluators,
        sessions=sessions,
        agent_keys=keys,
    )
    return TenantStores(
        tenant_id=tenant_id,
        session_factory=sf,
        users=UserStore(sf, tenant_id),
        workspaces=WorkspaceStore(sf, tenant_id),
        agents=agents,
        evaluators=evaluators,
        providers=LlmProviderStore(sf, tenant_id),
        sources=TraceSourceStore(sf, tenant_id, state.box),
        imports=TraceImportStore(sf, tenant_id),
        spans=spans,
        mappings=mappings,
        tokens=AgentTokens(sf, tenant_id),
        keys=keys,
        review=ReviewLog(sf, tenant_id),
        sessions=sessions,
        store=store,
        blob=blob,
        deps=deps,
    )


async def load_actor(session: AsyncSession, user: User) -> Actor:
    rows = (
        await session.execute(
            select(WorkspaceMember.workspace_id, WorkspaceMember.role).where(
                WorkspaceMember.user_id == user.id
            )
        )
    ).all()
    roles: dict[int, set[WorkspaceRole]] = {}
    for ws_id, role in rows:
        roles.setdefault(ws_id, set()).add(WorkspaceRole(role))
    return Actor(
        user.id,
        user.username,
        user.is_admin,
        user.tenant_id,
        {k: frozenset(v) for k, v in roles.items()},
    )


async def current_actor(session: SessionDep, user: CurrentUser) -> Actor:
    return await load_actor(session, user)


ActorDep = Annotated[Actor, Depends(current_actor)]


def tenant_stores(actor: ActorDep, request: Request) -> TenantStores:
    return build_tenant_stores(request.app.state, actor.tenant_id)


StoresDep = Annotated[TenantStores, Depends(tenant_stores)]


async def load_workspace_ref(session: AsyncSession, tenant_id: str, name: str) -> WorkspaceRef:
    return await load_ref(session, await workspace_row(session, tenant_id, name))


async def workspace_ref(stores: TenantStores, name: str) -> WorkspaceRef:
    async with stores.session() as session:
        return await load_workspace_ref(session, stores.tenant_id, name)


async def resolve_user(stores: TenantStores, username: str) -> User:
    async with stores.session() as session:
        user = await session.scalar(
            select(User).where(User.tenant_id == stores.tenant_id, User.username == username)
        )
    if user is None:
        raise HTTPException(status_code=404, detail=f"unknown user: {username}")
    return user


def require(allowed: bool, detail: str) -> None:
    if not allowed:
        raise HTTPException(status_code=403, detail=detail)


def ensure_active(ws: WorkspaceRef) -> None:
    if ws.archived:
        raise HTTPException(status_code=409, detail=f"workspace {ws.name} is archived")


async def writable_endpoint(
    stores: StoresDep,
    actor: ActorDep,
    workspace: str,
    *,
    rule: EndpointRule,
    api_key: bool = False,
    url: str | None = None,
) -> WorkspaceRef:
    ws = await workspace_ref(stores, workspace)
    require(can_write(actor, ws), f"you cannot write to @{workspace}")
    ensure_active(ws)
    if api_key and not ws.is_shared:
        raise HTTPException(status_code=400, detail=PERSONAL_KEY_NOTE)
    if url is not None and ws.is_shared:
        await check_endpoint(url, rule=rule)
    return ws


async def writable(stores: StoresDep, actor: ActorDep, workspace: str) -> WorkspaceRef:
    ws = await workspace_ref(stores, workspace)
    require(can_write(actor, ws), f"you cannot write to @{workspace}")
    ensure_active(ws)
    return ws
