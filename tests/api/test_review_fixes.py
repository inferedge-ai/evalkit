import asyncio
import hashlib

import httpx
import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete

from evalkit.api.app import create_app
from evalkit.contracts.enums import ExperimentStatus
from evalkit.contracts.harness import RunStatus
from evalkit.core import runner as runner_module
from evalkit.core.evaluators import presentation, rubrics, trajectory_rubrics
from evalkit.core.judge import JudgeUsage
from evalkit.core.runner import ExperimentRequest, evaluate_experiment, run_experiment
from evalkit.db.models import Evaluation
from evalkit.db.session import run_migrations, verify_schema
from tests.fake_harness import FakeHarness
from tests.native import (
    HOLDER,
    USERS,
    Env,
    seed_env,
    start,
    wait_for,
)

FINANCE = "finance@canonical"


@pytest.fixture
def satisfied_judges(monkeypatch):
    async def fake_judge(cfg, **kwargs):
        return (
            rubrics.RubricJudgeOutput(
                verdicts=[
                    rubrics.RubricVerdict(index=1, verdict="satisfied", source=None),
                    rubrics.RubricVerdict(index=2, verdict="satisfied", source=None),
                ]
            ),
            JudgeUsage(),
        )

    monkeypatch.setattr(rubrics, "call_judge", fake_judge)
    monkeypatch.setattr(presentation, "call_judge", fake_judge)
    monkeypatch.setattr(trajectory_rubrics, "call_judge", fake_judge)


async def _reopen(env: Env, exp_id: int) -> None:
    await env.store.transition(exp_id, ExperimentStatus.EVALUATING, HOLDER)
    await env.store.transition(exp_id, ExperimentStatus.FAILED, HOLDER, error="retry")
    await env.store.transition(exp_id, ExperimentStatus.RUNNING, HOLDER)


# ── review fix: schema verified, not migrated, at startup ─────────────────
def test_verify_schema_fails_without_migrations(fresh_db_url):
    db_url = fresh_db_url
    with pytest.raises(RuntimeError, match="db upgrade"):
        asyncio.run(verify_schema(db_url))
    run_migrations(db_url)
    asyncio.run(verify_schema(db_url))


# ── review fixes: SPA fallback, pinned cases, honest denominator ──────────
@pytest.fixture
async def api(tmp_path, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>evalkit spa</html>")
    (dist / "assets" / "index.js").write_text("// js")
    (dist / "favicon.svg").write_text("<svg/>")
    app = create_app(
        env.deps.cfg,  # type: ignore[arg-type]
        blob_store=env.blob,
        runner_deps=env.deps,
        agents=env.deps.agents,  # type: ignore[arg-type]
        session_factory=session_factory,
        check_schema=False,
        ui_dist=dist,
        holder=HOLDER,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/auth/login", json={"username": "admin", "password": USERS["admin"]}
            )
            client.headers["Authorization"] = f"Bearer {resp.json()['token']}"
            yield client, env


async def test_spa_history_fallback(api):
    client, _ = api
    for route in ["/", "/experiments/27", "/datasets/finance@canonical/cases"]:
        resp = await client.get(route)
        assert resp.status_code == 200, route
        assert "evalkit spa" in resp.text
    assert (await client.get("/favicon.svg")).text == "<svg/>"
    assert (await client.get("/assets/index.js")).status_code == 200
    assert (await client.get("/assets/missing.js")).status_code == 404
    assert (await client.get("/api/nope")).status_code == 404


async def test_spa_fallback_never_serves_files_outside_dist(api, tmp_path):
    client, _ = api
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret")
    encoded = str(secret).replace("/", "%2F")
    for route in [f"/{encoded}", "/..%2Fsecret.txt", "/%2E%2E/secret.txt", "/%2Fetc%2Fpasswd"]:
        resp = await client.get(route)
        assert resp.status_code == 200, route
        assert "evalkit spa" in resp.text, route
        assert "top secret" not in resp.text, route


# ── review fix: fixture key safety + upload safety ────────────────────────
def _registry(key: str, data: bytes) -> bytes:
    digest = hashlib.sha256(data).hexdigest()
    return f"fixtures:\n- key: {key}\n  sha256: {digest}\n  context: ctx\n".encode()


async def test_sync_rejects_non_flat_fixture_key(api):
    client, _ = api
    yaml_body = (
        "- id: sneaky\n"
        "  input: {question: 'q as of 2024-01-01'}\n"
        "  fixtures: ['../../secret.txt']\n"
    )
    resp = await client.post(
        "/api/sync",
        params={"dataset": "custom-1", "workspace": "canonical"},
        files=[
            ("files", ("fixtures.yaml", _registry("../../secret.txt", b"x"))),
            ("files", ("cases.yaml", yaml_body.encode())),
        ],
    )
    body = resp.json()
    assert body["aborted"]
    assert {f["code"] for f in body["findings"]} >= {"E-SCHEMA", "E-FIXTURE-UNKNOWN-KEY"}


async def test_sync_rejects_unsafe_upload_names(api):
    client, _ = api
    resp = await client.post(
        "/api/sync",
        params={"dataset": "custom-1", "workspace": "canonical"},
        files=[("files", ("../evil.yaml", b"- id: a\n  input: {question: q}\n"))],
    )
    assert resp.status_code == 400


# ── review fix: resume runs under the snapshotted model + capabilities ────
async def test_resume_uses_snapshotted_model_and_caps(tmp_path, satisfied_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness(fail_run=True))
    exp = await start(
        env, ExperimentRequest(dataset=FINANCE, agent="fake@canonical", model="pinned-model")
    )
    healthy = FakeHarness()
    healthy._counter = 100
    env.deps.harness_transport = httpx.ASGITransport(app=healthy.app)
    await _reopen(env, exp.id)
    await run_experiment(env.deps, exp.id, HOLDER)
    payloads = list(healthy.received_payloads.values())
    assert payloads and all(p.get("model") == "pinned-model" for p in payloads)


# ── review fix: evaluate reruns evaluators with partially published rows ──
async def test_evaluate_reruns_partial_multi_score(tmp_path, satisfied_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness())
    exp = await start(env, ExperimentRequest(dataset=FINANCE, agent="fake@canonical"))
    first = (await env.details(exp.id))[0]
    assert "answer_rubric_coverage" in {e.name for e in first.evaluations}
    async with session_factory() as s:
        await s.execute(
            delete(Evaluation).where(
                Evaluation.experiment_run_id == first.experiment_run_id,
                Evaluation.producer == "answer_rubrics",
                Evaluation.name != "answer_rubric_score",
            )
        )
        await s.commit()
    await evaluate_experiment(env.deps, exp.id)
    run = next(
        r for r in await env.details(exp.id) if r.experiment_run_id == first.experiment_run_id
    )
    names = {e.name for e in run.evaluations}
    assert "answer_rubric_coverage" in names and "answer_rubric_accuracy" in names


# ── review fix: completed run without an answer is a protocol violation ───
async def test_completed_without_answer_fails_run(tmp_path, satisfied_judges, session_factory):
    env = await seed_env(session_factory, tmp_path, FakeHarness(answer=None))
    exp = await start(env, ExperimentRequest(dataset=FINANCE, agent="fake@canonical"))
    for run in await env.details(exp.id):
        assert run.agent_run.status == "failed"
        assert run.agent_run.error and "protocol violation" in run.agent_run.error


def test_run_status_contract_rejects_completed_without_answer():
    with pytest.raises(PydanticValidationError, match="answer"):
        RunStatus.model_validate({"status": "completed", "model": "m"})
    RunStatus.model_validate({"status": "completed", "answer": "hi", "model": "m"})
    RunStatus.model_validate({"status": "failed", "error": "x", "model": "m"})


# ── review fix: background task failures persist `failed` on the experiment ─
async def test_background_evaluate_failure_marks_experiment_failed(
    api, satisfied_judges, monkeypatch
):
    client, _ = api
    resp = await client.post(
        "/api/experiments", json={"dataset": FINANCE, "agent": "fake@canonical"}
    )
    exp_id = resp.json()["id"]
    assert (await wait_for(client, exp_id))["status"] == "completed"

    async def dying_evaluate(deps, eid, holder=None, *, refetch_trajectories=False):
        raise RuntimeError("judge endpoint unreachable")

    monkeypatch.setattr(runner_module, "evaluate_experiment", dying_evaluate)
    assert (await client.post(f"/api/experiments/{exp_id}/evaluate")).status_code == 200
    status = await wait_for(client, exp_id, statuses=("failed",))
    assert "judge endpoint unreachable" in status["status_error"]
    assert status["lease_holder"] is None


# ── review item: FastAPI's own OpenAPI/Swagger stays reachable under the SPA ─
async def test_server_openapi_docs_reachable(api):
    client, _ = api
    resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    assert resp.json()["info"]["title"] == "evalkit"
    assert (await client.get("/docs")).status_code == 200
