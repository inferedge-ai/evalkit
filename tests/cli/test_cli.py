import asyncio
import hashlib
import io
import json
import os
import re
import subprocess
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from typer.testing import CliRunner

from evalkit.api.app import create_app
from evalkit.cli import main as main_module
from evalkit.cli import runs_commands
from evalkit.cli.client import ApiError
from evalkit.cli.main import app as cli_app
from evalkit.client import connection
from evalkit.contracts.enums import DatasetKind, SyncSource, TrajectorySource
from evalkit.core.harness import HarnessClient
from evalkit.core.importer import incoming_case
from evalkit.core.pull import safe_dir
from evalkit.db import models
from evalkit.db.fixture_blobs import record_blob
from evalkit.db.session import make_session_factory
from evalkit.db.store import PgStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from evalkit.store.base import CaseFile, Experiment, RunDetail
from tests.fake_agents import fake_agent, reference_mapping
from tests.fake_harness import FakeHarness
from tests.identity import seed_agent
from tests.native import CASE_EVALUATORS, HOLDER, seed_env, suite_case

runner = CliRunner()


@pytest.fixture(scope="module")
def stub_judges_module():
    from evalkit.core.evaluators import presentation, rubrics, trajectory_rubrics
    from evalkit.core.judge import JudgeUsage

    async def fake_judge(cfg, **kwargs):
        return (
            rubrics.RubricJudgeOutput(
                verdicts=[
                    rubrics.RubricVerdict(index=1, verdict="satisfied", source=None),
                    rubrics.RubricVerdict(
                        index=2, verdict="incorrect", note="wrong figure", source=None
                    ),
                ]
            ),
            JudgeUsage(),
        )

    originals = [
        (rubrics, rubrics.call_judge),
        (presentation, presentation.call_judge),
        (trajectory_rubrics, trajectory_rubrics.call_judge),
    ]
    for module, _ in originals:
        module.call_judge = fake_judge
    yield
    for module, original in originals:
        module.call_judge = original


def _serve(app) -> tuple[uvicorn.Server, threading.Thread, int]:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=uv_server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if uv_server.started:
            break
        time.sleep(0.05)
    port = uv_server.servers[0].sockets[0].getsockname()[1]
    return uv_server, thread, port


@pytest.fixture(scope="module")
def fake_harness():
    return FakeHarness(workspace_files={"outputs/report.md": b"# report"})


@pytest.fixture(scope="module")
def harness_url(fake_harness):
    uv_server, thread, port = _serve(fake_harness.app)
    yield f"http://127.0.0.1:{port}/evalkit/v1"
    uv_server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def server(tmp_path_factory, stub_judges_module, fake_harness, harness_url, module_db_url):
    tmp_path = tmp_path_factory.mktemp("cli")

    async def setup_db():
        factory = make_session_factory(module_db_url)
        env = await seed_env(factory, tmp_path, fake_harness)
        await UserStore(factory).create("ravi", "password")
        await seed_agent(factory, "fake", "admin", harness_url=harness_url)
        await seed_agent(factory, "fake", "ravi", harness_url=harness_url)
        await seed_agent(
            factory,
            "sdk",
            "admin",
            harness_url=None,
            trajectory_source=TrajectorySource.OTEL,
            mapping=reference_mapping(),
        )
        await factory.kw["bind"].dispose()
        return factory, env

    factory, env = asyncio.run(setup_db())
    application = create_app(
        env.deps.cfg,
        blob_store=env.blob,
        runner_deps=env.deps,
        session_factory=factory,
        check_schema=False,
        holder=HOLDER,
    )
    uv_server, thread, port = _serve(application)
    yield f"http://127.0.0.1:{port}", application
    uv_server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def env(server, tmp_path, monkeypatch, module_db_url):
    base_url, application = server
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "config.toml")
    resp = httpx.post(
        f"{base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    )
    token = resp.json()["token"]
    monkeypatch.setenv("EVALKIT_SERVER_URL", base_url)
    monkeypatch.setenv("EVALKIT_TOKEN", token)
    return base_url, application


def api_get(path: str, **params):
    resp = httpx.get(
        f"{os.environ['EVALKIT_SERVER_URL']}{path}",
        params=params,
        headers={"Authorization": f"Bearer {os.environ['EVALKIT_TOKEN']}"},
    )
    resp.raise_for_status()
    return resp.json()


def dataset_refs() -> set[str]:
    return {d["ref"] for d in api_get("/api/datasets")}


def latest_experiment(dataset: str) -> Experiment:
    [row] = api_get(f"/api/datasets/{dataset}/experiments", limit=1)
    return Experiment.model_validate(row)


def experiment_runs(exp_id: int) -> list[RunDetail]:
    return [RunDetail.model_validate(r) for r in api_get(f"/api/experiments/{exp_id}/runs")]


def in_db(url: str, work):
    async def go():
        factory = make_session_factory(url)
        async with factory() as session:
            result = await work(session)
            await session.commit()
        await factory.kw["bind"].dispose()
        return result

    return asyncio.run(go())


def _exp_id(output: str) -> int:
    return int(re.search(r"^experiment (\d+)", output, re.M).group(1))


def test_help_lists_commands():
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    for command in [
        "login",
        "update",
        "lint",
        "sync",
        "datasets",
        "fixtures",
        "run",
        "evaluate",
        "status",
        "summary",
        "runs",
        "harness",
        "users",
        "skills",
        "contracts",
        "serve",
        "db",
        "tokens",
        "whoami",
    ]:
        assert command in result.output


def test_whoami_shows_expiry_and_warns_when_close(env, monkeypatch):
    result = runner.invoke(cli_app, ["whoami"])
    assert result.exit_code == 0, result.output
    assert "admin on" in result.output and "token expires" in result.output
    assert "expires in" not in result.output

    real_warning = connection.token_expiry_warning
    monkeypatch.setattr(
        connection,
        "token_expiry_warning",
        lambda p, at, now: real_warning(p, at, now + timedelta(days=30)),
    )
    result = runner.invoke(cli_app, ["whoami"])
    assert "expires in 0.0h" in result.output and "EVALKIT_TOKEN" in result.output
    result = runner.invoke(cli_app, ["harness", "check", "fake@canonical"])
    assert "expires in 0.0h" in result.output

    monkeypatch.setenv("EVALKIT_TOKEN", "junk")
    result = runner.invoke(cli_app, ["whoami"])
    assert result.exit_code == 1 and "401" in result.output


def test_login_writes_the_config(server, tmp_path, monkeypatch):
    base_url, _ = server
    config_path = tmp_path / "login.toml"
    monkeypatch.setattr(connection, "CONFIG_PATH", config_path)
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    monkeypatch.delenv("EVALKIT_TOKEN", raising=False)
    result = runner.invoke(
        cli_app,
        ["login", "--url", base_url, "--username", "admin", "--password", "hunter2-pw"],
    )
    assert result.exit_code == 0, result.output
    text = config_path.read_text()
    assert "[profiles" not in text and f'url = "{base_url}"' in text
    assert "token = " in text and 'username = "admin"' in text


def test_login_replaces_the_stored_server(server, tmp_path, monkeypatch):
    base_url, _ = server
    config_path = tmp_path / "p.toml"
    monkeypatch.setattr(connection, "CONFIG_PATH", config_path)
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    monkeypatch.delenv("EVALKIT_TOKEN", raising=False)
    dead = runner.invoke(
        cli_app,
        ["login", "--url", "http://127.0.0.1:9", "--username", "x", "--password", "y"],
    )
    assert dead.exit_code == 1
    assert not config_path.exists()
    result = runner.invoke(
        cli_app,
        ["login", "--url", base_url, "--username", "admin", "--password", "hunter2-pw"],
    )
    assert result.exit_code == 0, result.output
    assert runner.invoke(cli_app, ["datasets", "list"]).exit_code == 0
    assert runner.invoke(cli_app, ["--profile", "dev", "datasets", "list"]).exit_code == 2
    assert runner.invoke(cli_app, ["profiles", "list"]).exit_code == 2
    config_path.write_text('url = "http://127.0.0.1:9"\ntoken = "t"\n')
    assert runner.invoke(cli_app, ["datasets", "list"]).exit_code == 1
    result = runner.invoke(
        cli_app,
        ["login", "--url", base_url, "--username", "admin", "--password", "hunter2-pw"],
    )
    assert result.exit_code == 0, result.output
    assert f'url = "{base_url}"' in config_path.read_text()
    assert runner.invoke(cli_app, ["datasets", "list"]).exit_code == 0


def test_lint_exits_one_on_errors(env, tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- id: a\n  tags: ['working:x']\n  input: {question: q}\n")
    result = runner.invoke(cli_app, ["lint", str(bad)])
    assert result.exit_code == 1
    assert "E-RESERVED-TAG" in result.output


def _run_experiment() -> int:
    result = runner.invoke(
        cli_app, ["run", "finance@canonical", "--agent", "fake@canonical", "--runs", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "completed" in result.output
    return _exp_id(result.output)


def test_run_prints_summary(env):
    base_url, _ = env
    result = runner.invoke(
        cli_app, ["run", "finance@canonical", "--agent", "fake@canonical", "--runs", "1"]
    )
    assert result.exit_code == 0, result.output
    exp_id = _exp_id(result.output)
    assert f"experiment {exp_id}: {base_url}/experiments/{exp_id}" in result.output
    assert "completed (2/2 ran, 2/2 evaluated)" in result.output
    assert "buckets:" in result.output
    assert "answer_rubric_score" in result.output


def test_status_and_summary(env):
    exp_id = _run_experiment()
    result = runner.invoke(cli_app, ["status", str(exp_id)])
    assert result.exit_code == 0, result.output
    exp = latest_experiment("finance@canonical")
    assert f"experiment {exp_id}  #{exp.number} of finance@canonical" in result.output
    assert "status=completed 2/2 ran, 2/2 evaluated" in result.output
    assert "held by" not in result.output
    result = runner.invoke(cli_app, ["summary", str(exp_id)])
    assert result.exit_code == 0
    assert "buckets:" in result.output


def test_status_shows_the_lease_holder_of_an_active_experiment(monkeypatch):
    expires = (datetime.now(UTC) + timedelta(seconds=45)).isoformat()
    body = {
        "id": 7,
        "number": 3,
        "dataset": "finance@dev",
        "agent": "fake@dev",
        "status": "running",
        "status_error": None,
        "lease_holder": "cli:ravi@laptop:42",
        "lease_expires_at": expires,
        "progress": {"total": 4, "ran": 1, "evaluated": 0},
    }

    class StubResponse:
        def json(self):
            return body

    class StubClient:
        def get(self, path, **kwargs):
            assert path == "/api/experiments/7"
            return StubResponse()

    monkeypatch.setattr(main_module, "_client", lambda: StubClient())
    result = runner.invoke(cli_app, ["status", "7"])
    assert result.exit_code == 0, result.output
    assert "experiment 7  #3 of finance@dev  agent=fake@dev" in result.output
    assert "status=running 1/4 ran, 0/4 evaluated" in result.output
    assert re.search(r"held by cli:ravi@laptop:42 \(lease expires in 4\ds\)", result.output)


def test_summary_lists_errored_cases(capsys):
    case = {"overall": None, "delta": None, "errors": {}, "run_errors": []}
    report = {
        "name": "#4",
        "experiment_id": "12",
        "dataset": "finance@dev",
        "buckets": {"presentation": None},
        "evaluators": [{"name": "presentation_judge", "score": None, "cases": 0, "errors": 2}],
        "cases": [
            {**case, "case_id": "case-1", "errors": {"presentation_judge": "403\nForbidden"}},
            {**case, "case_id": "case-2", "run_errors": ["agent crashed"]},
            {**case, "case_id": "case-3", "overall": 1.0},
        ],
        "gauges": [],
    }
    main_module._render_summary(report)
    out = capsys.readouterr().out
    assert "experiment 12  #4 of finance@dev" in out
    assert "(0 cases, 2 errored)" in out
    assert "errored cases:" in out
    assert re.search(r"case-1\s+presentation_judge: 403$", out, re.M)
    assert re.search(r"case-2\s+run: agent crashed$", out, re.M)
    assert "case-3" not in out


def test_runs_pull_experiment_writes_every_run_flat(env, tmp_path, monkeypatch):
    exp_id = _run_experiment()
    out = tmp_path / "pulled"
    result = runner.invoke(
        cli_app, ["runs", "pull", "--experiment", str(exp_id), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert f"pulled 2 run(s) to {out}" in result.output
    index = json.loads((out / "index.json").read_text())
    assert index["agent"] == "fake@canonical" and index["total_matching"] == 2
    for row in index["runs"]:
        assert row["source"] == "eval" and row["scores"]
        run_dir = out / row["dir"]
        assert {"run.json", "trajectory.json", "case.json", "evaluations.json"} <= {
            p.name for p in run_dir.iterdir()
        }
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["runs", "pull", "--experiment", str(exp_id)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "evalkit-runs" / f"experiment-{exp_id}" / "index.json").is_file()


def test_runs_pull_pages_merge_into_one_index(env, tmp_path):
    _run_experiment()
    out = tmp_path / "paged"
    base = ["runs", "pull", "fake@canonical", "--source", "eval", "--limit", "1", "--out", str(out)]
    first = runner.invoke(cli_app, [*base, "--offset", "0"])
    second = runner.invoke(cli_app, [*base, "--offset", "1"])
    assert first.exit_code == 0 and second.exit_code == 0, first.output + second.output
    index = json.loads((out / "index.json").read_text())
    assert len({row["run_id"] for row in index["runs"]}) == 2
    assert index["filters"]["offset"] == 1
    assert len(list((out / "runs").iterdir())) == 2


def test_runs_list_json_rows(env):
    _run_experiment()
    result = runner.invoke(
        cli_app, ["runs", "list", "fake@canonical", "--source", "eval", "--limit", "2", "--json"]
    )
    assert result.exit_code == 0, result.output
    rows = [json.loads(line) for line in result.stdout.splitlines()]
    assert len(rows) == 2 and all(row["source"] == "eval" for row in rows)
    assert "2 of " in result.stderr


def test_runs_files_lists_a_run_of_an_experiment_pull(env, tmp_path):
    exp_id = _run_experiment()
    out = tmp_path / "pulled"
    pulled = runner.invoke(
        cli_app, ["runs", "pull", "--experiment", str(exp_id), "--out", str(out)]
    )
    assert pulled.exit_code == 0, pulled.output
    runs = json.loads((out / "index.json").read_text())["runs"]
    run_id = runs[0]["run_id"]
    listed = runner.invoke(cli_app, ["runs", "files", str(out), run_id])
    assert listed.exit_code == 0, listed.output
    assert "fetch with --path" in listed.output
    assert (out / "runs" / safe_dir(run_id) / "files.json").is_file()
    unknown = runner.invoke(cli_app, ["runs", "files", str(out), "nope"])
    assert unknown.exit_code != 0 and "pull it first" in unknown.output


def test_runs_pull_warns_inside_an_unignored_git_repo(env, tmp_path, monkeypatch):
    exp_id = _run_experiment()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.chdir(repo)
    pulled = runner.invoke(cli_app, ["runs", "pull", "--experiment", str(exp_id)])
    assert pulled.exit_code == 0 and "not ignored" in pulled.output
    (repo / ".gitignore").write_text("evalkit-runs/\n")
    again = runner.invoke(cli_app, ["runs", "pull", "--experiment", str(exp_id)])
    assert again.exit_code == 0 and "not ignored" not in again.output


def test_skills_install(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills" / "demo-skill"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("---\nname: demo-skill\ndescription: d\n---\nbody\n")
    monkeypatch.setattr(main_module, "skills_root", lambda: tmp_path / "skills")
    target = tmp_path / "out"
    result = runner.invoke(cli_app, ["skills", "install", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert (target / "demo-skill" / "SKILL.md").is_file()


def test_contracts_export(tmp_path, monkeypatch):
    contracts_dir = tmp_path / "contracts"
    contracts_dir.mkdir()
    (contracts_dir / "harness-api-v1.json").write_text("{}")
    (contracts_dir / "atif-accepted.json").write_text("{}")
    monkeypatch.setattr(main_module, "contracts_root", lambda: contracts_dir)
    target = tmp_path / "out"
    result = runner.invoke(cli_app, ["contracts", "export", "--target", str(target)])
    assert result.exit_code == 0, result.output
    assert (target / "harness-api-v1.json").is_file()
    assert (target / "atif-accepted.json").is_file()


def test_datasets_list(env):
    result = runner.invoke(cli_app, ["datasets", "list"])
    assert result.exit_code == 0
    assert "finance" in result.output


def _write_registry(path, key: str, data: bytes) -> None:
    path.write_text(
        f"fixtures:\n- key: {key}\n  sha256: {hashlib.sha256(data).hexdigest()}\n  context: ctx\n"
    )


def _capture_post_files(monkeypatch, response: dict):
    sent: dict = {}

    class StubResponse:
        def __init__(self, body):
            self._body = body

        def json(self):
            return self._body

    class StubClient:
        def post_files(self, path, files, params=None, data=None):
            sent["path"] = path
            sent["data"] = data
            sent["names"] = sorted(name for name, _ in files)
            sent["params"] = params
            return StubResponse(response)

        def get(self, path, **kwargs):
            return StubResponse({"username": "admin"})

    monkeypatch.setenv("EVALKIT_SERVER_URL", "http://stub")
    monkeypatch.setenv("EVALKIT_TOKEN", "stub-token")
    monkeypatch.setattr(main_module, "_client", lambda: StubClient())
    return sent


SYNC_OK = {
    "created": 1,
    "patched": 0,
    "deleted": 0,
    "overlays_applied": 0,
    "findings": [],
    "aborted": False,
}


def test_dir_sync_uploads_yaml_and_registry_only(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, SYNC_OK)
    case_dir = tmp_path / "cases"
    (case_dir / "fixtures").mkdir(parents=True)
    (case_dir / "fixtures" / "doc.pdf").write_bytes(b"%PDF cli")
    _write_registry(case_dir / "fixtures.yaml", "doc.pdf", b"%PDF cli")
    (case_dir / "one.yaml").write_text(
        "- id: cli-fx\n  input: {question: 'summarize as of 2024-01-01'}\n  fixtures: [doc.pdf]\n"
    )
    (case_dir / "README.md").write_text("notes")
    result = runner.invoke(
        cli_app, ["sync", str(case_dir), "--workspace", "canonical", "--version", "v9"]
    )
    assert result.exit_code == 0, result.output
    assert sent["path"] == "/api/sync"
    assert sent["names"] == ["fixtures.yaml", "one.yaml"]
    assert sent["params"] == {
        "dataset": "cases",
        "workspace": "canonical",
        "prune": True,
        "sha": "v9",
    }


def test_single_file_sync_uploads_nearest_registry(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, SYNC_OK)
    root = tmp_path / "datasets"
    (root / "finance").mkdir(parents=True)
    _write_registry(root / "fixtures.yaml", "doc.pdf", b"x")
    (root / "finance" / "one.yaml").write_text(
        "- id: cli-fx\n  input: {question: 'q as of 2024-01-01'}\n  fixtures: [doc.pdf]\n"
    )
    result = runner.invoke(cli_app, ["sync", str(root / "finance" / "one.yaml")])
    assert result.exit_code == 0, result.output
    assert sent["names"] == ["fixtures.yaml", "one.yaml"]
    assert sent["params"] == {"dataset": "finance", "workspace": "admin", "prune": False}


def test_dir_sync_uploads_registry_from_parent(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, SYNC_OK)
    root = tmp_path / "datasets"
    (root / "finance").mkdir(parents=True)
    _write_registry(root / "fixtures.yaml", "doc.pdf", b"x")
    (root / "finance" / "one.yaml").write_text(
        "- id: cli-fx\n  input: {question: 'q as of 2024-01-01'}\n  fixtures: [doc.pdf]\n"
    )
    result = runner.invoke(cli_app, ["sync", str(root / "finance")])
    assert result.exit_code == 0, result.output
    assert sent["names"] == ["fixtures.yaml", "one.yaml"]


def test_subfolder_sync_keeps_dataset_name_and_relative_paths(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, SYNC_OK)
    root = tmp_path / "eval" / "cases"
    (root / "finance" / "equity").mkdir(parents=True)
    (tmp_path / "evalkit.yaml").write_text("cases: eval/cases\nfixtures: eval/fixtures\n")
    (root / "finance" / "equity" / "tcs.yaml").write_text(
        "- id: tcs-1\n  input: {question: 'q as of 2024-01-01'}\n"
    )
    (root / "finance" / "mf.yaml").write_text("- id: mf-1\n  input: {question: 'q 2024'}\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["sync", "eval/cases/finance/equity"])
    assert result.exit_code == 0, result.output
    assert sent["params"] == {"dataset": "finance", "workspace": "admin", "prune": False}
    assert sent["names"] == ["equity/tcs.yaml"]
    result = runner.invoke(cli_app, ["sync", "finance/equity/tcs.yaml"])
    assert result.exit_code == 0, result.output
    assert sent["params"]["dataset"] == "finance" and sent["names"] == ["equity/tcs.yaml"]
    result = runner.invoke(cli_app, ["sync", "finance"])
    assert sent["params"]["prune"] is True
    assert sent["names"] == ["equity/tcs.yaml", "mf.yaml"]
    result = runner.invoke(cli_app, ["sync", "finance/only-a-tag"])
    assert result.exit_code == 1
    assert "not a folder or file" in result.output
    result = runner.invoke(cli_app, ["sync", "eval/cases"])
    assert result.exit_code == 1
    assert "cases root" in result.output


def test_sync_ignores_unset_env_vars_in_local_config(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, SYNC_OK)
    monkeypatch.delenv("UNSET_COST_MAP", raising=False)
    root = tmp_path / "eval" / "cases"
    (root / "finance").mkdir(parents=True)
    (tmp_path / "evalkit.yaml").write_text(
        "cases: eval/cases\nfixtures: eval/fixtures\ncost_map:\n  source: env:UNSET_COST_MAP\n"
    )
    (root / "finance" / "mf.yaml").write_text("- id: mf-1\n  input: {question: 'q 2024'}\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["sync", "finance"])
    assert result.exit_code == 0, result.output
    assert sent["names"] == ["mf.yaml"]


def test_empty_dir_sync_sends_no_files(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, {**SYNC_OK, "created": 0, "deleted": 2})
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    result = runner.invoke(cli_app, ["sync", str(case_dir)])
    assert result.exit_code == 0, result.output
    assert sent["names"] == []
    assert sent["params"] == {"dataset": "cases", "workspace": "admin", "prune": True}


def test_sync_unknown_key_fails(env, tmp_path):
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    (case_dir / "one.yaml").write_text(
        "- id: cli-fx\n  input: {question: 'q as of 2024-01-01'}\n  fixtures: [nope.pdf]\n"
    )
    result = runner.invoke(cli_app, ["sync", str(case_dir)])
    assert result.exit_code == 1
    assert "E-FIXTURE-UNKNOWN-KEY" in result.output


def test_offline_lint_uses_nearest_registry_and_ignores_local_bytes(tmp_path):
    root = tmp_path / "datasets"
    (root / "finance").mkdir(parents=True)
    (root / "fixtures").mkdir()
    (root / "fixtures" / "doc.pdf").write_bytes(b"changed")
    _write_registry(root / "fixtures.yaml", "doc.pdf", b"original")
    (root / "finance" / "one.yaml").write_text(
        "- id: lint-fx\n  input: {question: 'q as of 2024-01-01'}\n"
        "  fixtures: [doc.pdf, missing.pdf]\n"
    )
    result = runner.invoke(cli_app, ["lint", "--offline", str(root / "finance")])
    assert result.exit_code == 1
    assert "E-FIXTURE-UNKNOWN-KEY" in result.output
    assert "missing.pdf" in result.output
    assert "E-FIXTURE-DIGEST" not in result.output


def test_offline_lint_unused_warning_only_when_registry_is_in_scope(tmp_path):
    root = tmp_path / "datasets"
    (root / "finance").mkdir(parents=True)
    (root / "fixtures.yaml").write_text(
        "fixtures:\n"
        f"- key: used.pdf\n  sha256: {hashlib.sha256(b'u').hexdigest()}\n  context: u\n"
        f"- key: other-suite.pdf\n  sha256: {hashlib.sha256(b'o').hexdigest()}\n  context: o\n"
    )
    (root / "finance" / "one.yaml").write_text(
        "- id: lint-fx\n  input: {question: 'q as of 2024-01-01'}\n  fixtures: [used.pdf]\n"
    )
    result = runner.invoke(cli_app, ["lint", "--offline", str(root / "finance")])
    assert result.exit_code == 0, result.output
    assert "W-FIXTURE-UNUSED" not in result.output

    result = runner.invoke(cli_app, ["lint", "--offline", str(root)])
    assert result.exit_code == 0, result.output
    assert "W-FIXTURE-UNUSED" in result.output and "other-suite.pdf" in result.output


def test_server_lint_uploads_nearest_registry_with_scope(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, {"findings": [], "errors": 0, "warnings": 0})
    root = tmp_path / "datasets"
    (root / "finance").mkdir(parents=True)
    _write_registry(root / "fixtures.yaml", "doc.pdf", b"x")
    (root / "finance" / "one.yaml").write_text(
        "- id: a\n  input: {question: 'q as of 2024-01-01'}\n  fixtures: [doc.pdf]\n"
    )
    result = runner.invoke(cli_app, ["lint", str(root / "finance")])
    assert result.exit_code == 0, result.output
    assert sent["names"] == ["fixtures.yaml", "one.yaml"]
    assert sent["params"] == {"warn_unused_fixtures": "false"}

    result = runner.invoke(cli_app, ["lint", str(root)])
    assert result.exit_code == 0, result.output
    assert sent["names"] == ["finance/one.yaml", "fixtures.yaml"]
    assert sent["params"] == {"warn_unused_fixtures": "true"}


def test_lint_passes_dataset_to_server(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, {"findings": [], "errors": 0, "warnings": 0})
    case_dir = tmp_path / "cases"
    case_dir.mkdir()
    (case_dir / "one.yaml").write_text("- id: a\n  input: {question: 'q as of 2024-01-01'}\n")
    result = runner.invoke(cli_app, ["lint", str(case_dir), "--dataset", "mine"])
    assert result.exit_code == 0, result.output
    assert sent["path"] == "/api/lint" and sent["params"] == {"dataset": "mine@admin"}


def test_removed_commands_are_gone():
    assert runner.invoke(cli_app, ["fixtures", "pack", "--out", "x.tar"]).exit_code == 2
    assert runner.invoke(cli_app, ["datasets", "install"]).exit_code == 2


def test_datasets_list_prints_last_sync_sha(monkeypatch):
    class StubResponse:
        def json(self):
            return [
                {
                    "ref": "finance@canonical",
                    "kind": "suite",
                    "last_sync": {"detail": {"sha": "abc123def4567890"}},
                },
                {"ref": "ff@dev", "kind": "suite", "last_sync": None},
            ]

    class StubClient:
        def get(self, path, **kwargs):
            return StubResponse()

    monkeypatch.setattr(main_module, "_client", lambda: StubClient())
    result = runner.invoke(cli_app, ["datasets", "list"])
    assert result.exit_code == 0, result.output
    assert "finance@canonical" in result.output and "kind=suite" in result.output
    assert "sha=abc123def456" in result.output and "sha=-" in result.output


def test_runs_pull_rejects_zip_slip(env, tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as malicious:
        malicious.writestr("index.json", '{"runs": [], "total_matching": 0}')
        malicious.writestr("../evil.txt", "escaped")

    class StubResponse:
        content = buffer.getvalue()

    class StubExperiment:
        def json(self):
            return {"agent": "fake@canonical"}

    class StubClient:
        def get(self, path, **kwargs):
            return StubExperiment()

        def post(self, path, **kwargs):
            return StubResponse()

    monkeypatch.setattr(main_module, "_client", lambda: StubClient())
    out = tmp_path / "review-out"
    result = runner.invoke(cli_app, ["runs", "pull", "--experiment", "7", "--out", str(out)])
    assert result.exit_code == 1
    assert "escapes the output directory" in result.output
    assert not (tmp_path / "evil.txt").exists()


def test_review_request_show_and_approve(env):
    out = runner.invoke(
        cli_app,
        ["review", "request", "finance@canonical", "--case-id", "case-2", "--reviewer", "admin"],
    )
    assert out.exit_code == 0, out.output
    assert "requested review from admin on 1 case(s)" in out.output

    shown = runner.invoke(cli_app, ["review", "show", "finance@canonical"])
    assert shown.exit_code == 0, shown.output
    assert re.search(r"case-2\s+review_requested\s+admin", shown.output)

    queue = runner.invoke(cli_app, ["review", "queue"])
    assert queue.exit_code == 0, queue.output
    assert "case-2" in queue.output

    commented = runner.invoke(
        cli_app, ["review", "comment", "finance@canonical", "case-2", "looks fine"]
    )
    assert commented.exit_code == 0, commented.output
    comment_id = re.search(r"comment (\d+)", commented.output).group(1)

    approved = runner.invoke(
        cli_app, ["review", "approve", "finance@canonical", "case-2", "--comment", "ok"]
    )
    assert approved.exit_code == 0, approved.output
    assert "case-2: approved" in approved.output

    resolved = runner.invoke(
        cli_app, ["review", "resolve", "finance@canonical", "case-2", comment_id]
    )
    assert resolved.exit_code == 0, resolved.output
    assert f"comment {comment_id} resolved" in resolved.output

    detail = runner.invoke(cli_app, ["review", "show", "finance@canonical", "case-2"])
    assert detail.exit_code == 0, detail.output
    assert "state: approved" in detail.output
    assert "looks fine" in detail.output and "[resolved]" in detail.output
    assert "review_requested" in detail.output and "approved" in detail.output

    empty = runner.invoke(cli_app, ["review", "queue"])
    assert "nothing waiting" in empty.output


def test_lint_agent_with_offline_is_a_usage_error(tmp_path):
    case = tmp_path / "c.yaml"
    case.write_text("- id: a\n  input: {question: 'q as of 2024-01-01'}\n")
    result = runner.invoke(cli_app, ["lint", "--offline", "--agent", "x", str(case)])
    assert result.exit_code != 0
    assert "--agent needs the server; drop --offline" in result.output


def write_local_config(tmp_path, harness_url: str) -> Path:
    cases = tmp_path / "eval" / "cases" / "finance"
    cases.mkdir(parents=True)
    (cases / "cases.yaml").write_text(
        "cases:\n- id: local-1\n  tags: [local]\n  input: {question: 'q local'}\n"
        "  evaluators:\n    output:\n      answer_rubrics:\n        rubrics:\n"
        "        - rubric: 'Says 42.'\n          type: factual_retrieval\n"
    )
    (tmp_path / "eval" / "fixtures").mkdir()
    cost_map = tmp_path / "cost_map.json"
    cost_map.write_text("{}")
    (tmp_path / "evalkit.yaml").write_text(
        "cases: eval/cases\nfixtures: eval/fixtures\n"
        f"cost_map:\n  source: {cost_map}\n"
        "credentials:\n  agents:\n    fake: env:CLI_AGENT_KEY\n"
        "  llm_providers:\n    http://judge.test: env:CLI_JUDGE_KEY\n"
    )
    (tmp_path / ".env").write_text("CLI_AGENT_KEY=client-key\nCLI_JUDGE_KEY=jk\n")
    return tmp_path


def _local_run(*args: str) -> int:
    result = runner.invoke(cli_app, ["run", *args])
    assert result.exit_code == 0, result.output
    return _exp_id(result.output)


def test_local_run_end_to_end(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "fake"])
    assert result.exit_code == 0, result.output
    assert "run local-1 #1 completed" in result.output
    assert "scored local-1 #1" in result.output
    assert "buckets:" in result.output
    dataset = api_get("/api/datasets/finance@admin")
    assert dataset["workspace"] == "admin" and dataset["kind"] == "suite"
    exp_id = _exp_id(result.output)
    exp = latest_experiment("finance@admin")
    assert exp.id == exp_id and exp.status == "completed"
    assert exp.agent == "fake@admin" and exp.workspace == "admin"
    assert exp.config.snapshot.evalkit_version
    [run] = experiment_runs(exp_id)
    assert run.agent_run.status == "completed"
    assert any(e.name == "answer_rubric_score" for e in run.evaluations)
    runs = api_get("/api/agents/fake@admin/runs")
    assert any(r["run_id"] == run.agent_run.harness_run_id for r in runs["runs"])


def test_local_evaluate_rescores(env, tmp_path, monkeypatch, harness_url, module_db_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    exp_id = _local_run("finance", "--agent", "fake")
    [run] = experiment_runs(exp_id)

    async def wipe(session):
        await session.execute(
            sa.delete(models.Evaluation).where(
                models.Evaluation.experiment_run_id == run.experiment_run_id
            )
        )

    in_db(module_db_url, wipe)
    assert experiment_runs(exp_id)[0].evaluations == []
    result = runner.invoke(cli_app, ["evaluate", str(exp_id)])
    assert result.exit_code == 0, result.output
    assert experiment_runs(exp_id)[0].evaluations
    assert latest_experiment("finance@admin").status == "completed"


def test_local_run_against_canonical_dataset_does_not_sync(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    before = dataset_refs()
    exp_id = _local_run("finance@canonical", "--agent", "fake")
    assert dataset_refs() == before
    exp = latest_experiment("finance@canonical")
    assert exp.id == exp_id and exp.agent == "fake@admin"
    assert exp.status == "completed"
    by_name = {e.name: e for e in experiment_runs(exp_id)[0].evaluations}
    assert by_name["required_tool_calls"].error == (
        "custom evaluator required_tool_calls is not configured"
    )


def test_sync_command_targets_workspace(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    (tmp_path / "eval" / "cases" / "finance").rename(tmp_path / "eval" / "cases" / "synced")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["sync", "synced"])
    assert result.exit_code == 0, result.output
    assert "synced@admin" in dataset_refs()
    result = runner.invoke(
        cli_app,
        [
            "sync",
            "synced",
            "--workspace",
            "canonical",
            "--version",
            "abc",
            "--branch",
            "develop",
            "--no-dirty",
        ],
    )
    assert result.exit_code == 0, result.output
    detail = api_get("/api/datasets/synced@canonical")["last_sync"]["detail"]
    assert (detail["sha"], detail["branch"], detail["dirty"]) == ("abc", "develop", False)


def test_local_commands_resolve_only_the_credentials_they_use(
    env, tmp_path, monkeypatch, harness_url
):
    write_local_config(tmp_path, harness_url)
    monkeypatch.delenv("CLI_AGENT_KEY", raising=False)
    monkeypatch.delenv("CLI_JUDGE_KEY", raising=False)
    envcheck = tmp_path / "eval" / "cases" / "envcheck"
    envcheck.mkdir()
    (envcheck / "c.yaml").write_text("cases:\n- id: env-1\n  input: {question: 'q 2024'}\n")
    dotenv = tmp_path / ".env"
    dotenv.write_text("CLI_JUDGE_KEY=jk\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["harness", "check", "fake"])
    assert result.exit_code == 1, result.output
    assert "CLI_AGENT_KEY" in result.output and "credentials.agents" in result.output
    result = runner.invoke(cli_app, ["run", "envcheck", "--agent", "fake"])
    assert result.exit_code == 1, result.output
    assert "CLI_AGENT_KEY" in result.output
    assert "envcheck@admin" not in dataset_refs()

    dotenv.write_text("CLI_AGENT_KEY=client-key\n")
    result = runner.invoke(cli_app, ["harness", "check", "fake"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "fake"])
    assert result.exit_code == 1, result.output
    assert "CLI_JUDGE_KEY" in result.output


def test_canonical_sync_reuses_bytes_already_uploaded(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    data = b"k,v\n1,2\n"
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / "eval" / "fixtures" / "promo.csv").write_bytes(data)
    (tmp_path / "eval" / "cases" / "fixtures.yaml").write_text(
        f"fixtures:\n- key: promo.csv\n  sha256: {digest}\n  context: ctx\n"
    )
    promo = tmp_path / "eval" / "cases" / "promo"
    promo.mkdir()
    (promo / "p.yaml").write_text(
        "cases:\n- id: promo-1\n  fixtures: [promo.csv]\n  input: {question: 'q 2024-01-01'}\n"
    )
    result = runner.invoke(cli_app, ["sync", "promo"])
    assert result.exit_code == 0, result.output
    (tmp_path / "eval" / "fixtures" / "promo.csv").unlink()
    result = runner.invoke(cli_app, ["sync", "promo", "--workspace", "canonical"])
    assert result.exit_code == 0, result.output
    assert "promoted" not in result.output
    assert "promo@canonical" in dataset_refs()
    [case] = api_get("/api/datasets/promo@canonical/cases")
    assert case["files"] == [{"key": "promo.csv", "sha256": digest, "context": "ctx"}]


def test_local_harness_check(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["harness", "check", "fake"])
    assert result.exit_code == 0, result.output
    assert "capabilities" in result.output and "✓" in result.output


def test_run_rejects_another_users_agent_before_syncing(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    before = dataset_refs()
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "fake@ravi"])
    assert result.exit_code == 1
    assert "ravi's workspace" in result.output
    assert dataset_refs() == before


def test_local_run_uploads_fixtures_and_sends_them_to_the_agent(
    env, tmp_path, monkeypatch, harness_url, fake_harness
):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "eval" / "fixtures" / "bs.csv").write_bytes(b"a,b\n1,2\n")
    digest = hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    (tmp_path / "eval" / "cases" / "fixtures.yaml").write_text(
        f"fixtures:\n- key: bs.csv\n  sha256: {digest}\n  context: Balance sheet.\n"
    )
    (tmp_path / "eval" / "cases" / "finance" / "fx.yaml").write_text(
        "cases:\n- id: local-fx\n  fixtures: [bs.csv]\n"
        "  input: {question: 'Net debt as of 2024-01-01?'}\n"
        "  evaluators:\n    output:\n      answer_rubrics:\n        rubrics:\n"
        "        - rubric: 'Says 42.'\n          type: factual_retrieval\n"
    )
    result = runner.invoke(cli_app, ["run", "finance/fx.yaml", "--agent", "fake"])
    assert result.exit_code == 0, result.output
    assert "run local-fx #1 completed" in result.output
    run_id = next(
        r
        for r, p in fake_harness.received_payloads.items()
        if p["input"]["question"].startswith("Net debt")
    )
    assert fake_harness.received_files[run_id] == [("bs.csv", b"a,b\n1,2\n")]


def test_run_and_harness_check_refuse_code_driven_agents(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "sdk"])
    assert result.exit_code == 1 and "run_experiment" in result.output
    result = runner.invoke(cli_app, ["harness", "check", "sdk"])
    assert result.exit_code == 1 and "evalkit traces check" in result.output


def test_evaluate_refuses_python_evaluator_experiments(
    env, tmp_path, monkeypatch, harness_url, module_db_url
):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    exp_id = _local_run("finance", "--agent", "fake")

    def set_code_evaluators(names: list[str]):
        async def work(session):
            row = await session.get(models.Experiment, exp_id)
            snapshot = {**row.config["snapshot"], "code_evaluators": names}
            row.config = {**row.config, "snapshot": snapshot}

        in_db(module_db_url, work)

    set_code_evaluators(["cite"])
    result = runner.invoke(cli_app, ["evaluate", str(exp_id)])
    assert result.exit_code == 1 and "cite" in result.output
    assert "evaluate_experiment" in result.output
    set_code_evaluators([])
    refetch = runner.invoke(cli_app, ["evaluate", str(exp_id), "--refetch-trajectories"])
    assert refetch.exit_code == 0, refetch.output


def _seed_canonical(url: str, dataset: str) -> None:
    digest = hashlib.sha256(dataset.encode()).hexdigest()

    async def seed():
        factory = make_session_factory(url)
        async with factory() as session:
            await record_blob(session, digest, 1, None)
            await session.commit()
        canonical = await WorkspaceStore(factory).get_by_name("canonical")
        admin = {u.username: u.id for u in await UserStore(factory).list()}["admin"]
        doc = CaseFile(key="doc.pdf", sha256=digest, context="c")
        await PgStore(factory).sync(
            workspace=canonical,
            dataset=dataset,
            kind=DatasetKind.SUITE,
            incoming=[
                incoming_case(
                    suite_case(f"{dataset}-1", "q1", evaluators=CASE_EVALUATORS, tags=["plain"]),
                    source="s.yaml",
                    files=[],
                    extra_tags=[],
                ),
                incoming_case(
                    suite_case(f"{dataset}-2", "q2", evaluators=CASE_EVALUATORS, tags=["filed"]),
                    source="s.yaml",
                    files=[doc],
                    extra_tags=[],
                ),
            ],
            overlays=[],
            full=True,
            actor_id=admin,
            source=SyncSource.GIT,
            detail={},
        )
        await factory.kw["bind"].dispose()

    asyncio.run(seed())


def test_datasets_list_shows_a_canonical_datasets_cases(env, module_db_url):
    _seed_canonical(module_db_url, "listed")
    result = runner.invoke(cli_app, ["datasets", "list", "listed@canonical"])
    assert result.exit_code == 0, result.output
    assert re.search(r"listed-1\s+unreviewed\s+tags=plain fixtures=-", result.output)
    assert re.search(r"listed-2\s+unreviewed\s+tags=filed fixtures=doc.pdf", result.output)
    assert "2 case(s) in listed" in result.output
    listing = ["datasets", "list", "listed@canonical"]
    approved = runner.invoke(cli_app, [*listing, "--state", "approved"])
    assert approved.exit_code == 0, approved.output
    assert "listed-1" not in approved.output and "0 case(s)" in approved.output
    tagged = runner.invoke(cli_app, ["datasets", "list", "listed@canonical", "--tag", "filed"])
    assert "listed-1" not in tagged.output and "1 case(s)" in tagged.output
    unfiltered = runner.invoke(cli_app, ["datasets", "list", "--tag", "plain"])
    assert unfiltered.exit_code == 1 and "pass a dataset" in unfiltered.output


def test_datasets_list_shows_a_workspace_datasets_fixtures(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    data = b"a,b\n1,2\n"
    (tmp_path / "eval" / "fixtures" / "bs.csv").write_bytes(data)
    (tmp_path / "eval" / "cases" / "fixtures.yaml").write_text(
        f"fixtures:\n- key: bs.csv\n  sha256: {hashlib.sha256(data).hexdigest()}\n  context: c\n"
    )
    listing = tmp_path / "eval" / "cases" / "listing"
    listing.mkdir()
    (listing / "l.yaml").write_text(
        "cases:\n- id: listed-1\n  tags: [mine]\n  fixtures: [bs.csv]\n"
        "  input: {question: 'q 2024-01-01'}\n"
    )
    assert runner.invoke(cli_app, ["sync", "listing"]).exit_code == 0
    result = runner.invoke(cli_app, ["datasets", "list", "listing"])
    assert result.exit_code == 0, result.output
    assert re.search(r"listed-1\s+-\s+tags=mine fixtures=bs.csv", result.output)
    state = runner.invoke(cli_app, ["datasets", "list", "listing", "--state", "approved"])
    assert state.exit_code == 1 and "no review state" in state.output


def test_canonical_local_run_without_cases_or_fixtures(
    env, tmp_path, monkeypatch, harness_url, module_db_url
):
    write_local_config(tmp_path, harness_url)
    config = tmp_path / "evalkit.yaml"
    paths = "cases: eval/cases\nfixtures: eval/fixtures\n"
    config.write_text(config.read_text().replace(paths, ""))
    monkeypatch.chdir(tmp_path)
    _seed_canonical(module_db_url, "shared")
    args = ["run", "shared@canonical", "--agent", "fake", "--case-id", "shared-1"]
    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    own = runner.invoke(cli_app, ["run", "finance", "--agent", "fake"])
    assert own.exit_code == 1 and "set `cases:` in evalkit.yaml" in own.output
    synced = runner.invoke(cli_app, ["sync", "finance"])
    assert synced.exit_code == 1 and "set `cases:` in evalkit.yaml" in synced.output
    on_disk = runner.invoke(cli_app, ["sync", "eval/cases/finance"])
    assert on_disk.exit_code == 0, on_disk.output


def test_local_run_with_failed_scoring_says_how_to_finish(env, tmp_path, monkeypatch, harness_url):
    _, application = env
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    store = application.state.store
    real_publish = store.publish_evaluations

    async def publish_failing(experiment_run_id, rows, *, force, holder=None):
        raise RuntimeError("store down")

    monkeypatch.setattr(store, "publish_evaluations", publish_failing)
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "fake"])
    assert result.exit_code == 1, result.output
    exp = latest_experiment("finance@admin")
    assert exp.status == "failed"
    assert "scoring failed for 1 of 1 runs" in result.output
    assert f"finish it with: evalkit evaluate {exp.id}" in result.output

    monkeypatch.setattr(store, "publish_evaluations", real_publish)
    result = runner.invoke(cli_app, ["evaluate", str(exp.id)])
    assert result.exit_code == 0, result.output
    assert experiment_runs(exp.id)[0].evaluations
    assert latest_experiment("finance@admin").status == "completed"


def test_missing_env_is_a_one_line_error(tmp_path, monkeypatch):
    monkeypatch.delenv("LITELLM_BASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    local = tmp_path / "evalkit.yaml"
    local.write_text("cost_map:\n  source: env:LITELLM_BASE_URL\n")
    result = runner.invoke(cli_app, ["evaluate", "1"])
    assert result.exit_code == 1
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert result.output.strip() == (
        f"{local}: cost_map.source needs env LITELLM_BASE_URL (or env:LITELLM_BASE_URL:<default>)"
    )

    server_config = tmp_path / "config.yaml"
    server_config.write_text("db:\n  url: env:LITELLM_BASE_URL\n")
    for args in (
        ["serve", "--config", str(server_config)],
        ["db", "upgrade", "--config", str(server_config)],
    ):
        result = runner.invoke(cli_app, args)
        assert result.exit_code == 1
        assert result.output.strip() == (
            f"{server_config}: db.url needs env LITELLM_BASE_URL "
            "(or env:LITELLM_BASE_URL:<default>)"
        )


def test_agents_commands(env, tmp_path):
    result = runner.invoke(
        cli_app,
        ["agents", "create", "cliagent", "--harness-url", "http://h", "--max-concurrent-runs", "2"],
    )
    assert result.exit_code == 0, result.output
    assert "cliagent@admin" in result.output and "driven by cli" in result.output
    result = runner.invoke(cli_app, ["agents", "edit", "cliagent", "--trajectory-wait", "9"])
    assert result.exit_code == 0, result.output
    shown = runner.invoke(cli_app, ["agents", "show", "cliagent@admin"]).output
    assert "max_concurrent_runs" in shown and " 2" in shown and "trajectory_wait_seconds" in shown
    refused = runner.invoke(cli_app, ["agents", "edit", "cliagent", "--provides", "x=true"])
    assert refused.exit_code == 1 and "provides" in refused.output
    mapping = tmp_path / "m.json"
    mapping.write_text(json.dumps(reference_mapping()))
    result = runner.invoke(
        cli_app,
        ["agents", "create", "clitraced", "--trajectory-source", "otel", "--mapping", str(mapping)],
    )
    assert result.exit_code == 0, result.output
    missing = runner.invoke(cli_app, ["agents", "create", "nomap", "--trajectory-source", "otel"])
    assert missing.exit_code == 1 and "mapping" in missing.output
    result = runner.invoke(cli_app, ["agents", "edit", "clitraced", "--provides", "x=true"])
    assert result.exit_code == 0, result.output
    assert "{'x': True}" in runner.invoke(cli_app, ["agents", "show", "clitraced"]).output
    listed = runner.invoke(cli_app, ["agents", "list", "--workspace", "admin"]).output
    assert "cliagent@admin" in listed and "clitraced@admin" in listed
    assert runner.invoke(cli_app, ["agents", "archive", "cliagent"]).exit_code == 0
    assert "cliagent@admin" not in runner.invoke(cli_app, ["agents", "list"]).output
    bad = runner.invoke(cli_app, ["agents", "edit", "clitraced", "--provides", "x"])
    assert bad.exit_code == 1 and "key=true" in bad.output


def test_run_judge_flags_start_from_the_server_default(env, tmp_path, monkeypatch, harness_url):
    default = api_get("/api/judge-default")
    assert default["model"] == "judge-model"
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    _local_run("finance", "--agent", "fake", "--judge-model", "x", "--judge-effort", "low")
    judge = latest_experiment("finance@admin").config.judge
    assert (judge.model, judge.reasoning_effort, judge.base_url) == (
        "x",
        "low",
        default["base_url"],
    )


def test_runs_pull_app_runs_of_a_personal_harness_agent(
    env, tmp_path, monkeypatch, harness_url, fake_harness
):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    exp_id = _local_run("finance", "--agent", "fake")
    eval_ids = {
        r["agent_run"]["harness_run_id"] for r in api_get(f"/api/experiments/{exp_id}/runs")
    }
    fake_harness.runs["app-run-x"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    out = tmp_path / "app"
    result = runner.invoke(
        cli_app, ["runs", "pull", "fake", "--source", "app", "--limit", "500", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    index = json.loads((out / "index.json").read_text())
    ids = {row["run_id"] for row in index["runs"]}
    assert "app-run-x" in ids and not ids & eval_ids
    assert (out / "runs" / "app-run-x" / "trajectory.json").is_file()


def test_runs_list_and_run_id_pull_for_a_personal_harness_agent(
    env, tmp_path, monkeypatch, harness_url, fake_harness
):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    fake_harness.runs["app-run-y"] = {"polls": 0, "stopped": False, "payload": {}, "model": "m"}
    listed = runner.invoke(
        cli_app, ["runs", "list", "fake", "--source", "app", "--limit", "500", "--json"]
    )
    assert listed.exit_code == 0, listed.output
    assert "app-run-y" in {json.loads(line)["run_id"] for line in listed.stdout.splitlines()}
    out = tmp_path / "one"
    pulled = runner.invoke(
        cli_app, ["runs", "pull", "fake", "--run-id", "app-run-y", "--out", str(out)]
    )
    assert pulled.exit_code == 0, pulled.output
    index = json.loads((out / "index.json").read_text())
    (row,) = index["runs"]
    assert row["run_id"] == "app-run-y" and row["pull_error"] is None
    assert index["pulled_at"] and index["total_matching"] == 1
    assert (out / "runs" / "app-run-y" / "trajectory.json").is_file()
    run_dir = out / "runs" / "app-run-y"
    assert not (run_dir / "files").exists()
    listed_files = runner.invoke(cli_app, ["runs", "files", str(out), "app-run-y"])
    assert listed_files.exit_code == 0, listed_files.output
    assert "outputs/report.md" in listed_files.output
    tree = json.loads((run_dir / "files.json").read_text())["files"]
    assert "outputs/report.md" in {entry["path"] for entry in tree}
    assert not (run_dir / "files").exists()
    fetched = runner.invoke(cli_app, ["runs", "files", str(out), "app-run-y", "--path", "outputs"])
    assert fetched.exit_code == 0, fetched.output
    assert (run_dir / "files" / "outputs" / "report.md").is_file()
    (run_dir / "files" / "outputs" / "report.md").unlink()
    args = ["runs", "files", str(out), "app-run-y", "--path", "outputs/report.md"]
    partial = runner.invoke(cli_app, [*args, "--path", "no/such"])
    assert partial.exit_code != 0
    assert partial.stdout.splitlines()[-1] == "fetching no/such"
    assert "no/such: no such file on the harness" in partial.stderr
    assert (run_dir / "files" / "outputs" / "report.md").is_file()
    fake_harness.capabilities["provides"]["workspace"] = False
    no_workspace = runner.invoke(cli_app, ["runs", "files", str(out), "app-run-y"])
    assert no_workspace.exit_code != 0
    assert "does not expose workspace files" in no_workspace.output
    missing = runner.invoke(
        cli_app, ["runs", "pull", "fake", "--run-id", "nope", "--out", str(out)]
    )
    assert missing.exit_code == 0 and "could not be fetched in full" in missing.output


def test_runs_list_notes_a_code_driven_agent_without_traces(env, monkeypatch):
    definition = fake_agent("task", "admin", shared=False, harness_url=None)
    monkeypatch.setattr(main_module, "_agent_definition", lambda ref: definition)
    monkeypatch.setattr(
        main_module, "_connection", lambda: connection.Connection("u", "t", "admin")
    )

    class StubClient:
        def get(self, path, **kwargs):
            return httpx.Response(200, json={"runs": [], "total": 0})

    monkeypatch.setattr(main_module, "_client", lambda: StubClient())
    result = runner.invoke(cli_app, ["runs", "list", "task", "--source", "app"])
    assert result.exit_code == 0, result.output
    assert "only sees its eval runs" in result.output


def test_runs_pull_pages_server_calls_above_100(env, tmp_path, monkeypatch):
    calls: list[dict] = []

    def page(body: dict) -> bytes:
        buffer = io.BytesIO()
        rows = [{"run_id": f"r{body['offset'] + i}"} for i in range(body["limit"])]
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("index.json", json.dumps({"runs": rows, "total_matching": 500}))
        return buffer.getvalue()

    class StubClient:
        def post(self, path, json=None, **kwargs):
            calls.append(json)
            return httpx.Response(200, content=page(json))

    monkeypatch.setattr(main_module, "_client", lambda: StubClient())
    monkeypatch.setattr(main_module, "_agent_definition", lambda ref: fake_agent())
    monkeypatch.setattr(
        main_module, "_connection", lambda: connection.Connection("u", "t", "admin")
    )
    out = tmp_path / "big"
    result = runner.invoke(
        cli_app, ["runs", "pull", "fake@canonical", "--limit", "150", "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert [(c["limit"], c["offset"]) for c in calls] == [(100, 0), (50, 100)]
    index = json.loads((out / "index.json").read_text())
    assert len(index["runs"]) == 150 and index["filters"]["limit"] == 150
    assert index["filters"]["offset"] == 0 and index["total_matching"] == 500


def test_parse_when_accepts_relative_and_iso_and_rejects_words():
    now = datetime(2026, 10, 15, tzinfo=UTC)
    assert runs_commands.parse_when("7d", now) == datetime(2026, 10, 8, tzinfo=UTC)
    assert runs_commands.parse_when("2026-10-08", now) == datetime(2026, 10, 8, tzinfo=UTC)
    assert runs_commands.parse_when("2026-10-08T10:00Z", now) == datetime(
        2026, 10, 8, 10, tzinfo=UTC
    )
    with pytest.raises(ApiError, match="not a time"):
        runs_commands.parse_when("yesterday", now)


def test_runs_list_personal_agent_with_a_down_harness_prints_a_note(
    env, tmp_path, monkeypatch, harness_url
):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)

    async def down(self, params=None):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(HarnessClient, "list_runs", down)
    result = runner.invoke(cli_app, ["runs", "list", "fake", "--source", "app"])
    assert result.exit_code == 0, result.output
    assert "note: harness unreachable" in result.output


def test_merged_rows_sort_by_time_across_offsets():
    rows = [
        {"run_id": "a", "created_at": "2026-10-08T10:00:00+05:30"},
        {"run_id": "b", "created_at": "2026-10-08T06:00:00Z"},
        {"run_id": "c", "created_at": None},
    ]
    ordered = sorted(rows, key=runs_commands._sort_key, reverse=True)
    assert [row["run_id"] for row in ordered] == ["b", "a", "c"]


def test_runs_files_picks_folders_and_keeps_unlisted_paths():
    listing = [{"path": "outputs/a.md"}, {"path": "outputs/b/c.md"}, {"path": "notes.md"}]
    picked = runs_commands._picked(listing, ["outputs", "notes.md", ".spill/full.json"])
    assert picked == ["outputs/a.md", "outputs/b/c.md", "notes.md", ".spill/full.json"]
    assert runs_commands._picked(listing, ["/"]) == ["outputs/a.md", "outputs/b/c.md", "notes.md"]
    with pytest.raises(ApiError):
        runs_commands._picked([], ["/"])


async def test_runs_files_refuses_an_unsafe_run_id_before_calling_the_harness():
    harness = HarnessClient(fake_agent(), None)
    with pytest.raises(ApiError, match="must be one plain path segment"):
        await runs_commands._harness_listing(harness, "../x")
    with pytest.raises(ApiError, match="must be one plain path segment"):
        await runs_commands._harness_fetch(harness, "a/b", ["f.txt"], lambda name, data: None)
