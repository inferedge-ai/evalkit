from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from evalkit.api.auth import CurrentUser
from evalkit.config import Config
from evalkit.core.endpoints import EndpointRule
from evalkit.db.session import tenant_session


def get_config(request: Request) -> Config:
    return request.app.state.cfg


def get_holder(request: Request) -> str:
    return request.app.state.holder


async def get_session(request: Request, user: CurrentUser) -> AsyncIterator[AsyncSession]:
    async with tenant_session(request.app.state.session_factory, user.tenant_id) as session:
        yield session


ConfigDep = Annotated[Config, Depends(get_config)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
HolderDep = Annotated[str, Depends(get_holder)]


def get_allow_private_endpoints(cfg: ConfigDep) -> bool:
    return cfg.allow_private_endpoints


AllowPrivateEndpointsDep = Annotated[bool, Depends(get_allow_private_endpoints)]


def get_endpoint_rule(cfg: ConfigDep, allow_private: AllowPrivateEndpointsDep) -> EndpointRule:
    return EndpointRule(allow_private, tuple(cfg.trusted_endpoints))


EndpointRuleDep = Annotated[EndpointRule, Depends(get_endpoint_rule)]
