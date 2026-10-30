import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
from click.testing import Result
from typer.testing import CliRunner

from evalkit.api.app import create_app
from evalkit.cli.main import app as cli_app
from evalkit.client import connection
from evalkit.core.runner import RunnerDeps
from evalkit.credentials import CredentialRegistry, CredentialsConfig
from evalkit.db.agents import AgentStore
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.session import make_session_factory
from evalkit.db.store import PgStore
from evalkit.db.workspaces import WorkspaceStore
from tests.cli.test_cli import _serve, stub_judges_module
from tests.fake_agents import JUDGE_URL, TEST_ENV
from tests.fake_blob import DictBlobStore
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent, seed_evaluators, seed_identity
from tests.native import HOLDER, custom_eval_app, make_cfg

__all__ = ["stub_judges_module"]

USERS = {"admin": "hunter2-pw", "alice": "password", "akshat": "password", "ravi": "password"}
CREDENTIALS = CredentialsConfig(
    agents={"ouragent@dev": "env:FAKE_AGENT_KEY", "ouragent@prod": "env:FAKE_AGENT_KEY"},
    evaluators={"required_tool_calls@dev": "env:CUSTOM_EVAL_KEY"},
    judges={JUDGE_URL: "env:JUDGE_KEY"},
)
RUBRICS = (
    "    evaluators:\n      output:\n        answer_rubrics:\n          rubrics:\n"
    "          - rubric: 'Says 42.'\n            type: factual_retrieval\n"
)
runner = CliRunner()


@dataclass
class Live:
    url: str
    db_url: str
    fake: FakeHarness
    blob: DictBlobStore


def cases_yaml(*case_ids: str, question: str = "What is the answer as of 2024-01-01?") -> str:
    return "cases:\n" + "".join(
        f"  - id: {case_id}\n    input: {{question: '{question} ({case_id})'}}\n{RUBRICS}"
        for case_id in case_ids
    )


def build_live(tmp_path_factory, db_url: str, fake: FakeHarness | None = None) -> Iterator[Live]:
    tmp = tmp_path_factory.mktemp("flows")
    harness = fake or FakeHarness()
    blob = DictBlobStore()

    async def setup():
        factory = make_session_factory(db_url)
        await seed_identity(factory, USERS, shared=("dev",), reviewers=["ravi"], writers=["alice"])
        await WorkspaceStore(factory).create_shared("prod", None)
        await seed_agent(factory, "ouragent", "dev")
        await seed_agent(factory, "ouragent", "prod")
        await seed_evaluators(factory, "dev")
        await factory.kw["bind"].dispose()
        return factory

    factory = asyncio.run(setup())
    cfg = make_cfg(tmp).model_copy(update={"credentials": CREDENTIALS})
    deps = RunnerDeps(
        cfg=cfg,
        store=PgStore(factory),
        blob_store=blob,
        agents=AgentStore(factory),
        evaluators=RemoteEvaluatorStore(factory),
        credentials=CredentialRegistry(CREDENTIALS, TEST_ENV),
        harness_transport=httpx.ASGITransport(app=harness.app),
        custom_eval_transport=httpx.ASGITransport(app=custom_eval_app()),
    )
    application = create_app(
        cfg,
        blob_store=blob,
        runner_deps=deps,
        session_factory=factory,
        check_schema=False,
        holder=HOLDER,
    )
    server, thread, port = _serve(application)
    yield Live(f"http://127.0.0.1:{port}", db_url, harness, blob)
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def live(tmp_path_factory, stub_judges_module, module_db_url) -> Iterator[Live]:
    yield from build_live(tmp_path_factory, module_db_url)


def token(live: Live, user: str) -> str:
    resp = httpx.post(
        f"{live.url}/api/auth/login", json={"username": user, "password": USERS[user]}
    )
    resp.raise_for_status()
    return resp.json()["token"]


def api(live: Live, user: str) -> httpx.Client:
    return httpx.Client(
        base_url=live.url, headers={"Authorization": f"Bearer {token(live, user)}"}, timeout=30
    )


class Cli:
    def __init__(self, live: Live, monkeypatch, tmp_path: Path):
        self.live = live
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "config.toml")
        monkeypatch.chdir(tmp_path)
        (tmp_path / "suites").mkdir()
        (tmp_path / "fixtures").mkdir()
        (tmp_path / "evalkit.yaml").write_text("cases: suites\nfixtures: fixtures\n")
        self.as_user("admin")

    def as_user(self, user: str) -> None:
        self.monkeypatch.setenv("EVALKIT_SERVER_URL", self.live.url)
        self.monkeypatch.setenv("EVALKIT_TOKEN", token(self.live, user))

    def __call__(self, *args: str, input: str | None = None) -> Result:
        return runner.invoke(cli_app, list(args), input=input)

    def ok(self, *args: str) -> str:
        result = self(*args)
        assert result.exit_code == 0, result.output
        return result.output

    def fails(self, *args: str) -> str:
        result = self(*args)
        assert result.exit_code != 0, result.output
        return result.output

    def suite(self, name: str, files: dict[str, str], registry: str | None = None) -> Path:
        root = self.tmp_path / "suites" / name
        for relative, text in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        if registry is not None:
            (root / "fixtures.yaml").write_text(registry)
        return root


@pytest.fixture
def cli(live, monkeypatch, tmp_path) -> Cli:
    return Cli(live, monkeypatch, tmp_path)


def sync_counts(output: str) -> dict[str, int]:
    found = re.search(r"created=(\d+) patched=(\d+) deleted=(\d+) overlays_applied=(\d+)", output)
    assert found, output
    return dict(
        zip(
            ("created", "patched", "deleted", "overlays_applied"),
            map(int, found.groups()),
            strict=True,
        )
    )


def experiment_id(output: str) -> int:
    found = re.search(r"^experiment (\d+)", output, re.M)
    assert found, output
    return int(found.group(1))
