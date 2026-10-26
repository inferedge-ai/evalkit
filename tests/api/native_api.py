from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from tests.fake_harness import FakeHarness
from tests.identity import seed_agent
from tests.native import Env, seed_env, serve

CAPS = FakeHarness().capabilities


@asynccontextmanager
async def api_env(
    tmp_path, session_factory, fake: FakeHarness | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, Env]]:
    env = await seed_env(session_factory, tmp_path, fake or FakeHarness())
    await seed_agent(session_factory, "fake", "admin")
    await seed_agent(session_factory, "fake", "alice")
    async with serve(env, tmp_path) as client:
        yield client, env


async def login_as(client: httpx.AsyncClient, username: str, password: str) -> httpx.AsyncClient:
    other = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    resp = await other.post("/api/auth/login", json={"username": username, "password": password})
    other.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    return other
