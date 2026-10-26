import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.responses import JSONResponse

from evalkit.api.app import create_app
from evalkit.config import (
    AuthConfig,
    BlobConfig,
    Config,
    CostMapConfig,
    DbConfig,
    ServerConfig,
)
from evalkit.contracts.cases import Case
from evalkit.contracts.enums import DatasetKind, SyncSource
from evalkit.contracts.settings import JudgeSettings
from evalkit.core.agents import explicit
from evalkit.core.blob import fixture_blob_key
from evalkit.core.importer import incoming_case
from evalkit.core.review import CaseReview
from evalkit.core.runner import (
    ExperimentRequest,
    RunnerDeps,
    harness_capabilities,
    prepare_experiment,
    run_experiment,
)
from evalkit.core.workspace import parse_slug
from evalkit.db.agents import AgentStore
from evalkit.db.evaluators import RemoteEvaluatorStore
from evalkit.db.fixture_blobs import record_blob
from evalkit.db.review import ReviewLog
from evalkit.db.store import PgStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from evalkit.store.base import CaseFile, Experiment, RunDetail, Selection
from tests.fake_agents import JUDGE_URL, TEST_CREDENTIALS_CONFIG, fake_credentials
from tests.fake_blob import DictBlobStore
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent, seed_evaluators, seed_identity

HOLDER = "pod:test"
USERS = {"admin": "hunter2-pw", "alice": "password", "akshat": "password"}

CASE_EVALUATORS = {
    "output": {
        "answer_rubrics": {
            "rubrics": [
                {"rubric": "States revenue of $1.2B.", "type": "factual_retrieval"},
                {"rubric": "Computes the CAGR.", "type": "derived_computation"},
            ]
        },
        "max_response_time": {"max_seconds": 600},
    },
    "custom": {"required_tool_calls": {"tools": ["retrieve_passages"]}},
}

FILE_BYTES = b"section,line_item\nMeta,Company\n"
FILE_DIGEST = hashlib.sha256(FILE_BYTES).hexdigest()


def make_cfg(tmp_path) -> Config:
    cost_map_path = tmp_path / "cost_map.json"
    cost_map_path.write_text(
        json.dumps({"judge-model": {"input_cost_per_token": 1e-6, "output_cost_per_token": 2e-6}})
    )
    return Config(
        server=ServerConfig(base_url="http://evalkit.test"),
        db=DbConfig(url="postgresql+psycopg://unused/evalkit"),
        blob=BlobConfig(url=f"file://{tmp_path}/blob"),
        judge_default=JudgeSettings(base_url=JUDGE_URL, model="judge-model"),
        cost_map=CostMapConfig(source=str(cost_map_path)),
        credentials=TEST_CREDENTIALS_CONFIG,
        auth=AuthConfig(),
    )


RECEIVED_EVAL_QUESTIONS: list[str] = []
RECEIVED_EVAL_RUNS: list[dict] = []


def custom_eval_app() -> FastAPI:
    app = FastAPI()

    @app.post("/rtc")
    async def rtc(payload: dict):
        assert payload["case"]["config"]
        RECEIVED_EVAL_QUESTIONS.append(payload["case"]["input"]["question"])
        RECEIVED_EVAL_RUNS.append(payload["run"])
        return {
            "scores": [
                {"name": "required_tool_calls", "score": 1.0, "label": "pass"},
                {"name": "tool_call_efficiency", "score": 0.5},
            ]
        }

    @app.post("/broken")
    async def broken():
        return JSONResponse({"detail": "boom"}, status_code=500)

    return app


@dataclass
class Env:
    session_factory: async_sessionmaker[AsyncSession]
    store: PgStore
    blob: DictBlobStore
    deps: RunnerDeps
    user_ids: dict[str, int]
    review: ReviewLog

    async def details(self, exp_id: int) -> list[RunDetail]:
        return await self.store.run_details(exp_id)

    async def experiment(self, exp_id: int) -> Experiment:
        return await self.store.get_experiment(exp_id)


def suite_case(
    case_id: str,
    question: str,
    *,
    evaluators: dict | None = None,
    tags: Iterable[str] = ("finance",),
    fixtures: Iterable[str] = (),
) -> Case:
    return Case.model_validate(
        {
            "id": case_id,
            "input": {"question": question},
            "tags": list(tags),
            "fixtures": list(fixtures),
            "evaluators": CASE_EVALUATORS if evaluators is None else evaluators,
        }
    )


async def sync_cases(
    env: Env,
    cases: list[Case],
    *,
    dataset: str = "finance",
    workspace: str = "canonical",
    files: dict[str, CaseFile] | None = None,
    full: bool = False,
    kind: DatasetKind = DatasetKind.SUITE,
    actor: str = "admin",
) -> None:
    ws = await WorkspaceStore(env.session_factory).get_by_name(workspace)
    await env.store.sync(
        workspace=ws,
        dataset=dataset,
        kind=kind,
        incoming=[
            incoming_case(
                case,
                source=f"{dataset}.yaml",
                files=[(files or {})[key] for key in case.fixtures],
                extra_tags=[],
            )
            for case in cases
        ],
        overlays=[],
        full=full,
        actor_id=env.user_ids[actor],
        source=SyncSource.GIT,
        detail={},
    )


async def approve(
    env: Env, dataset: str = "finance@canonical", case_ids: Iterable[str] | None = None
) -> None:
    wanted = None if case_ids is None else set(case_ids)
    cases = await env.store.cases_for(dataset, Selection(all=True), agent=None, approved_only=False)
    await env.review.append(
        [
            CaseReview.unreviewed(dataset, case.case_id, case.content_hash).approve("admin")
            for case in cases
            if wanted is None or case.case_id in wanted
        ]
    )


async def put_fixture(env: Env, data: bytes = FILE_BYTES, key: str = "bs.csv") -> CaseFile:
    digest = hashlib.sha256(data).hexdigest()
    await env.blob.put_object(fixture_blob_key(digest), data)
    async with env.session_factory() as s:
        await record_blob(s, digest, len(data), env.user_ids["admin"])
        await s.commit()
    return CaseFile(key=key, sha256=digest, context="Balance sheet.")


async def seed_env(
    session_factory: async_sessionmaker[AsyncSession],
    tmp_path,
    fake: FakeHarness | None = None,
    *,
    broken_on_case_2: bool = False,
    approved: bool = True,
    blob: DictBlobStore | None = None,
) -> Env:
    await seed_identity(session_factory, USERS, reviewers=["admin"])
    await seed_agent(session_factory)
    await seed_agent(session_factory, "fake", "akshat")
    await seed_evaluators(session_factory)
    user_ids = {u.username: u.id for u in await UserStore(session_factory).list()}
    store = PgStore(session_factory)
    blob_store = blob or DictBlobStore()
    deps = RunnerDeps(
        cfg=make_cfg(tmp_path),
        store=store,
        blob_store=blob_store,
        agents=AgentStore(session_factory),
        evaluators=RemoteEvaluatorStore(session_factory),
        credentials=fake_credentials(),
        harness_transport=httpx.ASGITransport(app=(fake or FakeHarness()).app),
        custom_eval_transport=httpx.ASGITransport(app=custom_eval_app()),
    )
    env = Env(session_factory, store, blob_store, deps, user_ids, ReviewLog(session_factory))
    evaluators_2 = dict(CASE_EVALUATORS)
    if broken_on_case_2:
        evaluators_2 = {
            **CASE_EVALUATORS,
            "custom": {**CASE_EVALUATORS["custom"], "broken_eval": {"x": 1}},
        }
    await sync_cases(
        env,
        [suite_case("case-1", "q1"), suite_case("case-2", "q2", evaluators=evaluators_2)],
    )
    if approved:
        await approve(env)
    return env


async def prepare(
    env: Env,
    req: ExperimentRequest,
    *,
    runner: str = "admin",
    holder: str = HOLDER,
) -> Experiment:
    deps = env.deps
    agent = await deps.agents.get(explicit(parse_slug(req.agent)))
    caps = req.capabilities or await harness_capabilities(
        agent, deps.credentials.key_for("agent", agent.ref), deps.harness_transport
    )
    exp, _ = await prepare_experiment(
        env.store,
        req,
        agent=agent,
        caps=caps,
        remote_evaluators=await deps.evaluators.in_workspace(agent.workspace),
        judge=req.judge or deps.cfg.judge_default,
        runner=runner,
        holder=holder,
    )
    return exp


async def start(env: Env, req: ExperimentRequest, *, holder: str = HOLDER) -> Experiment:
    exp = await prepare(env, req, holder=holder)
    await run_experiment(env.deps, exp.id, holder)
    return await env.experiment(exp.id)


@asynccontextmanager
async def serve(env: Env, tmp_path, *, user: str = "admin") -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(
        env.deps.cfg,  # type: ignore[arg-type]
        blob_store=env.blob,
        runner_deps=env.deps,
        agents=env.deps.agents,  # type: ignore[arg-type]
        session_factory=env.session_factory,
        check_schema=False,
        holder=HOLDER,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            resp = await client.post(
                "/api/auth/login", json={"username": user, "password": USERS[user]}
            )
            client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
            yield client


async def login_as(client: httpx.AsyncClient, username: str) -> httpx.AsyncClient:
    other = httpx.AsyncClient(transport=client._transport, base_url="http://test")
    resp = await other.post(
        "/api/auth/login", json={"username": username, "password": USERS[username]}
    )
    other.headers["Authorization"] = f"Bearer {resp.json()['token']}"
    return other


async def wait_for(
    client: httpx.AsyncClient, exp_id: int, statuses=("completed", "failed")
) -> dict:
    for _ in range(400):
        body = (await client.get(f"/api/experiments/{exp_id}")).json()
        if body["status"] in statuses:
            return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"experiment {exp_id} stuck in {body['status']}")
