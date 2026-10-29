from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.config import Config
from evalkit.core.runner import RunnerDeps
from evalkit.db.agents import AgentStore
from evalkit.db.keys import KeyLoader
from evalkit.db.mappings import MappingStore
from evalkit.db.review import ReviewLog
from evalkit.db.sessions import DbSessionReader
from evalkit.db.spans import SpanStore
from evalkit.db.store import PgStore
from evalkit.db.tokens import AgentTokens


def get_config(request: Request) -> Config:
    return request.app.state.cfg


def get_runner_deps(request: Request) -> RunnerDeps:
    return request.app.state.runner_deps


def get_keys(request: Request) -> KeyLoader:
    return request.app.state.keys


def get_review_log(request: Request) -> ReviewLog:
    return request.app.state.review_log


def get_store(request: Request) -> PgStore:
    return request.app.state.store


def get_holder(request: Request) -> str:
    return request.app.state.holder


def get_agents(request: Request) -> AgentStore:
    return request.app.state.agents


def get_tokens(request: Request) -> AgentTokens:
    return request.app.state.tokens


def get_spans(request: Request) -> SpanStore:
    return request.app.state.spans


def get_mappings(request: Request) -> MappingStore:
    return request.app.state.mappings


def get_sessions(request: Request) -> DbSessionReader:
    return request.app.state.sessions


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


ConfigDep = Annotated[Config, Depends(get_config)]
RunnerDepsDep = Annotated[RunnerDeps, Depends(get_runner_deps)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
ReviewLogDep = Annotated[ReviewLog, Depends(get_review_log)]
PgStoreDep = Annotated[PgStore, Depends(get_store)]
HolderDep = Annotated[str, Depends(get_holder)]
AgentStoreDep = Annotated[AgentStore, Depends(get_agents)]
AgentTokensDep = Annotated[AgentTokens, Depends(get_tokens)]
SpanStoreDep = Annotated[SpanStore, Depends(get_spans)]
MappingStoreDep = Annotated[MappingStore, Depends(get_mappings)]
SessionReaderDep = Annotated[DbSessionReader, Depends(get_sessions)]
KeysDep = Annotated[KeyLoader, Depends(get_keys)]
