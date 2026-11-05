from evalkit.contracts.enums import TokenKind
from evalkit.db.auth import find_token
from evalkit.db.models import Agent, AuthToken, TenantSettings, User, Workspace
from evalkit.db.session import ALL_TENANTS, tenant_session
from evalkit.db.tokens import token_hash
from evalkit.db.users import any_admin_exists


async def _seed(raw_session_factory) -> tuple[int, int]:
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(TenantSettings(tenant_id=2))
        akshat = User(tenant_id=1, username="akshat", password_hash="x")
        eve = User(tenant_id=2, username="eve", password_hash="x")
        s.add_all([akshat, eve])
        await s.flush()
        ws = Workspace(tenant_id=2, name="eve", owner_user_id=eve.id)
        s.add(ws)
        await s.flush()
        agent = Agent(workspace_id=ws.id, name="bot", config={})
        s.add(agent)
        await s.flush()
        s.add(AuthToken(user_id=akshat.id, token_hash=token_hash("t-akshat"), kind=TokenKind.USER))
        s.add(
            AuthToken(
                user_id=eve.id,
                token_hash=token_hash("t-bot"),
                kind=TokenKind.AGENT,
                agent_id=agent.id,
            )
        )
        await s.commit()
        return akshat.id, eve.id


async def test_find_token_reads_the_tenant_through_the_user(raw_session_factory):
    akshat_id, _ = await _seed(raw_session_factory)
    found = await find_token(raw_session_factory, token_hash("t-akshat"))
    assert found is not None
    row, tenant_id = found
    assert (row.user_id, tenant_id) == (akshat_id, 1)


async def test_find_token_reads_an_agent_tokens_tenant_through_the_workspace(raw_session_factory):
    await _seed(raw_session_factory)
    found = await find_token(raw_session_factory, token_hash("t-bot"))
    assert found is not None
    assert found[1] == 2


async def test_find_token_unknown(raw_session_factory):
    assert await find_token(raw_session_factory, token_hash("nope")) is None


async def test_any_admin_exists_looks_across_tenants(raw_session_factory):
    assert not await any_admin_exists(raw_session_factory)
    async with tenant_session(raw_session_factory, ALL_TENANTS) as s:
        s.add(TenantSettings(tenant_id=2))
        s.add(User(tenant_id=2, username="root", password_hash="x", is_admin=True))
        await s.commit()
    assert await any_admin_exists(raw_session_factory)
