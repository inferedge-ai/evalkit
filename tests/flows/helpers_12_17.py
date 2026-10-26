import asyncio
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass

import httpx
import uvicorn
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner, Result

from evalkit.api.app import create_app
from evalkit.cli.main import app as cli_app
from evalkit.client import connection
from evalkit.credentials import CredentialRegistry, CredentialsConfig
from evalkit.db.session import make_session_factory
from evalkit.db.workspaces import WorkspaceStore
from tests.fake_agents import TEST_CREDENTIALS_CONFIG, TEST_ENV
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent, seed_evaluators
from tests.native import HOLDER, USERS, Env, approve, seed_env, serve, suite_case, sync_cases

DEV = "dev"
FLOW_CREDENTIALS = CredentialsConfig(
    agents={
        **TEST_CREDENTIALS_CONFIG.agents,
        "ouragent@dev": "env:FAKE_AGENT_KEY",
        "myagent@akshat": "env:FAKE_AGENT_KEY",
    },
    evaluators={
        **TEST_CREDENTIALS_CONFIG.evaluators,
        "required_tool_calls@dev": "env:CUSTOM_EVAL_KEY",
    },
    judges=TEST_CREDENTIALS_CONFIG.judges,
)
cli = CliRunner()


async def seed_flows(
    session_factory: async_sessionmaker[AsyncSession], tmp_path, fake: FakeHarness
) -> Env:
    env = await seed_env(session_factory, tmp_path, fake)
    await WorkspaceStore(session_factory).create_shared(DEV, env.user_ids["admin"])
    await seed_agent(session_factory, "ouragent", DEV)
    await seed_evaluators(session_factory, DEV)
    await sync_cases(env, [suite_case("case-1", "q1"), suite_case("case-2", "q2")], workspace=DEV)
    await approve(env, f"finance@{DEV}")
    env.deps.credentials = CredentialRegistry(FLOW_CREDENTIALS, TEST_ENV)
    return env


@asynccontextmanager
async def flow_api(
    tmp_path, session_factory: async_sessionmaker[AsyncSession], fake: FakeHarness | None = None
) -> AsyncIterator[tuple[httpx.AsyncClient, Env]]:
    env = await seed_flows(session_factory, tmp_path, fake or FakeHarness())
    async with serve(env, tmp_path) as client:
        yield client, env


def _serve(app) -> tuple[uvicorn.Server, threading.Thread, int]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return server, thread, server.servers[0].sockets[0].getsockname()[1]


@dataclass
class Live:
    url: str
    app: object
    harness_url: str
    fake: FakeHarness
    monkeypatch: object

    def token(self, user: str) -> str:
        resp = httpx.post(
            f"{self.url}/api/auth/login", json={"username": user, "password": USERS[user]}
        )
        resp.raise_for_status()
        return resp.json()["token"]

    def as_user(self, user: str) -> None:
        self.monkeypatch.setenv("EVALKIT_TOKEN", self.token(user))

    def http(self, user: str = "admin") -> httpx.Client:
        return httpx.Client(
            base_url=self.url, headers={"Authorization": f"Bearer {self.token(user)}"}
        )

    def run(self, *args: str, input: str | None = None) -> Result:
        return cli.invoke(cli_app, list(args), input=input)


@contextmanager
def live_stack(db_url: str, tmp_path, monkeypatch) -> Iterator[Live]:
    fake = FakeHarness()
    harness, harness_thread, harness_port = _serve(fake.app)
    harness_url = f"http://127.0.0.1:{harness_port}/evalkit/v1"

    async def setup():
        factory = make_session_factory(db_url)
        env = await seed_flows(factory, tmp_path, fake)
        await seed_agent(factory, "myagent", "akshat", harness_url=harness_url)
        await factory.kw["bind"].dispose()
        return factory, env

    factory, env = asyncio.run(setup())
    env.deps.cfg.otel.settle_seconds = 0
    app = create_app(
        env.deps.cfg,
        blob_store=env.blob,
        runner_deps=env.deps,
        session_factory=factory,
        check_schema=False,
        holder=HOLDER,
    )
    server, thread, port = _serve(app)
    url = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setenv("EVALKIT_SERVER_URL", url)
    live = Live(url, app, harness_url, fake, monkeypatch)
    live.as_user("admin")
    yield live
    server.should_exit = True
    thread.join(timeout=5)
    harness.should_exit = True
    harness_thread.join(timeout=5)
