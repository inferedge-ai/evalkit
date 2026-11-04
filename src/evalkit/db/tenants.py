from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from evalkit.db.locks import lock_name
from evalkit.db.models import Tenant, User
from evalkit.db.session import ALL_TENANTS, tenant_session


class TenantExists(Exception):
    pass


async def find_identity(
    session_factory: async_sessionmaker[AsyncSession],
    external_id: str,
    issuer: str,
    subject: str,
) -> tuple[int | None, User | None]:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        row = (
            await session.execute(
                select(Tenant.id, User)
                .outerjoin(
                    User,
                    (User.tenant_id == Tenant.id)
                    & (User.issuer == issuer)
                    & (User.subject == subject),
                )
                .where(Tenant.external_id == external_id)
            )
        ).first()
    if row is None:
        return None, None
    return row[0], row[1]


async def create_tenant(
    session_factory: async_sessionmaker[AsyncSession],
    name: str,
    external_id: str | None,
    *,
    exist_ok: bool = False,
) -> int:
    async with tenant_session(session_factory, ALL_TENANTS) as session:
        await lock_name(session, "tenants")
        if external_id is not None:
            found = await session.scalar(select(Tenant.id).where(Tenant.external_id == external_id))
            if found is not None:
                if exist_ok:
                    return found
                raise TenantExists(f"a tenant with external id {external_id} exists")
        if await session.scalar(select(Tenant.id).where(Tenant.name == name)) is not None:
            raise TenantExists(f"a tenant named {name} exists")
        tenant = Tenant(name=name, external_id=external_id)
        session.add(tenant)
        await session.commit()
        return tenant.id
