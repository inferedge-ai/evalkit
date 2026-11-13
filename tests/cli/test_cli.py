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
from types import SimpleNamespace

import httpx2
import pytest
import sqlalchemy as sa
import uvicorn
from sqlalchemy import create_engine, text
from typer.testing import CliRunner

from evalkit.api import experiments as experiments_api
from evalkit.api.app import create_app
from evalkit.cli import local_run, runs_commands
from evalkit.cli import main as main_module
from evalkit.cli.client import ApiError
from evalkit.cli.main import app as cli_app
from evalkit.client import connection
from evalkit.client import oidc as client_oidc
from evalkit.client.oidc import ClientCredentialsRefused, EvalkitAuth, LoginExpired
from evalkit.contracts.enums import DatasetKind, SyncSource, TrajectorySource
from evalkit.core.harness import HarnessClient
from evalkit.core.importer import incoming_case
from evalkit.core.pull import safe_dir
from evalkit.db import models
from evalkit.db.fixture_blobs import record_blob
from evalkit.db.session import make_session_factory, tenant_session
from evalkit.db.store import PgStore
from evalkit.db.users import UserStore
from evalkit.db.workspaces import WorkspaceStore
from evalkit.store.base import CaseFile, Experiment, RunDetail
from tests.fake_agents import fake_agent, reference_mapping
from tests.fake_harness import FakeHarness
from tests.identity import FakeIdp, seed_agent
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
        await UserStore(factory, "default").create("ravi", password="password")
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
    monkeypatch.setenv("CLI_AGENT_KEY", "client-key")
    monkeypatch.setenv("CLI_JUDGE_KEY", "jk")
    resp = httpx2.post(
        f"{base_url}/api/auth/login", json={"username": "admin", "password": "hunter2-pw"}
    )
    token = resp.json()["token"]
    monkeypatch.setenv("EVALKIT_SERVER_URL", base_url)
    monkeypatch.setenv("EVALKIT_TOKEN", token)
    return base_url, application


def api_get(path: str, **params):
    resp = httpx2.get(
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
        async with tenant_session(factory, "default") as session:
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


def test_datasets_list_by_workspace(env):
    shared = runner.invoke(cli_app, ["datasets", "list", "--workspace", "canonical"])
    assert shared.exit_code == 0, shared.output
    assert "finance@canonical" in shared.output
    empty = runner.invoke(cli_app, ["datasets", "list", "--workspace", "admin"])
    assert empty.exit_code == 0, empty.output
    assert "finance@canonical" not in empty.output
    at_form = runner.invoke(cli_app, ["datasets", "list", "--workspace", "@canonical"])
    assert "finance@canonical" in at_form.output
    assert runner.invoke(cli_app, ["datasets", "list", "--workspace", "nope"]).exit_code == 1
    both = runner.invoke(cli_app, ["datasets", "list", "finance@canonical", "--workspace", "dev"])
    assert both.exit_code == 1


def test_datasets_show(env):
    shown = runner.invoke(cli_app, ["datasets", "show", "finance@canonical"])
    assert shown.exit_code == 0, shown.output
    assert "dataset: finance@canonical" in shown.output
    assert re.search(r"cases: \d+", shown.output)
    assert "review: " in shown.output and "tags: " in shown.output
    assert runner.invoke(cli_app, ["datasets", "show", "nope@canonical"]).exit_code == 1


def _write_registry(path, key: str, data: bytes) -> None:
    path.write_text(
        f"fixtures:\n- key: {key}\n  sha256: {hashlib.sha256(data).hexdigest()}\n  context: ctx\n"
    )


def _capture_post_files(monkeypatch, response: dict):
    sent: dict = {}

    class StubResponse:
        status_code = 200

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

        def head(self, path, **kwargs):
            return StubResponse({})

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
    root = tmp_path / "evalkit" / "cases"
    (root / "finance" / "equity").mkdir(parents=True)
    (tmp_path / "evalkit.yaml").write_text("cases: evalkit/cases\nfixtures: evalkit/fixtures\n")
    (root / "finance" / "equity" / "tcs.yaml").write_text(
        "- id: tcs-1\n  input: {question: 'q as of 2024-01-01'}\n"
    )
    (root / "finance" / "mf.yaml").write_text("- id: mf-1\n  input: {question: 'q 2024'}\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["sync", "evalkit/cases/finance/equity"])
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
    result = runner.invoke(cli_app, ["sync", "evalkit/cases"])
    assert result.exit_code == 1
    assert "cases root" in result.output


def test_sync_ignores_unset_env_vars_in_local_config(tmp_path, monkeypatch):
    sent = _capture_post_files(monkeypatch, SYNC_OK)
    monkeypatch.delenv("UNSET_COST_MAP", raising=False)
    root = tmp_path / "evalkit" / "cases"
    (root / "finance").mkdir(parents=True)
    (tmp_path / "evalkit.yaml").write_text(
        "cases: evalkit/cases\nfixtures: evalkit/fixtures\n"
        "cost_map:\n  source: env:UNSET_COST_MAP\n"
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
    in_dataset = runner.invoke(cli_app, ["review", "queue", "finance@canonical"])
    assert in_dataset.exit_code == 0 and "case-2" in in_dataset.output
    assert runner.invoke(cli_app, ["review", "queue", "other@canonical"]).exit_code == 1
    elsewhere = runner.invoke(cli_app, ["review", "queue", "--workspace", "admin"])
    assert "nothing waiting" in elsewhere.output
    assert runner.invoke(cli_app, ["review", "queue", "--workspace", "nope"]).exit_code == 1

    commented = runner.invoke(
        cli_app, ["review", "comment", "finance@canonical", "case-2", "--comment", "looks fine"]
    )
    assert commented.exit_code == 0, commented.output
    comment_id = re.search(r"comment (\d+)", commented.output).group(1)

    neither = runner.invoke(cli_app, ["review", "approve", "finance@canonical"])
    assert neither.exit_code == 1
    approved = runner.invoke(
        cli_app, ["review", "approve", "finance@canonical", "--all-waiting", "--comment", "ok"]
    )
    assert approved.exit_code == 0, approved.output
    assert "approving 1 case(s) in finance@canonical" in approved.output
    assert "case-2: approved" in approved.output
    again = runner.invoke(cli_app, ["review", "approve", "finance@canonical", "--all-waiting"])
    assert "nothing waiting on you" in again.output

    wrong_case = runner.invoke(
        cli_app, ["review", "resolve", "finance@canonical", "case-1", comment_id]
    )
    assert wrong_case.exit_code == 1
    resolved = runner.invoke(cli_app, ["review", "resolve", comment_id])
    assert resolved.exit_code == 0, resolved.output
    assert f"comment {comment_id} resolved" in resolved.output

    detail = runner.invoke(cli_app, ["review", "show", "finance@canonical", "case-2"])
    assert detail.exit_code == 0, detail.output
    assert "state: approved" in detail.output
    assert "id: case-2" in detail.output
    assert "looks fine" in detail.output and "[resolved]" in detail.output
    assert "review_requested" in detail.output and "approved" in detail.output

    empty = runner.invoke(cli_app, ["review", "queue"])
    assert "nothing waiting" in empty.output

    only_yaml = runner.invoke(cli_app, ["review", "show", "finance@canonical", "case-2", "--yaml"])
    assert only_yaml.exit_code == 0, only_yaml.output
    assert only_yaml.output.startswith("id: case-2\n") and "changelog" not in only_yaml.output


def test_lint_agent_with_offline_is_a_usage_error(tmp_path):
    case = tmp_path / "c.yaml"
    case.write_text("- id: a\n  input: {question: 'q as of 2024-01-01'}\n")
    result = runner.invoke(cli_app, ["lint", "--offline", "--agent", "x", str(case)])
    assert result.exit_code != 0
    assert "--agent needs the server; drop --offline" in result.output


def write_local_config(tmp_path, harness_url: str) -> Path:
    cases = tmp_path / "evalkit" / "cases" / "finance"
    cases.mkdir(parents=True)
    (cases / "cases.yaml").write_text(
        "cases:\n- id: local-1\n  tags: [local]\n  input: {question: 'q local'}\n"
        "  evaluators:\n    output:\n      answer_rubrics:\n        rubrics:\n"
        "        - rubric: 'Says 42.'\n          type: factual_retrieval\n"
    )
    (tmp_path / "evalkit" / "fixtures").mkdir()
    cost_map = tmp_path / "cost_map.json"
    cost_map.write_text("{}")
    (tmp_path / "evalkit.yaml").write_text(
        "cases: evalkit/cases\nfixtures: evalkit/fixtures\n"
        f"cost_map:\n  source: {cost_map}\n"
        "credentials:\n  agents:\n    fake: env:CLI_AGENT_KEY\n"
    )
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
    (tmp_path / "evalkit" / "cases" / "finance").rename(tmp_path / "evalkit" / "cases" / "synced")
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
    envcheck = tmp_path / "evalkit" / "cases" / "envcheck"
    envcheck.mkdir()
    (envcheck / "c.yaml").write_text("cases:\n- id: env-1\n  input: {question: 'q 2024'}\n")
    (tmp_path / ".env").write_text("CLI_AGENT_KEY=client-key\nCLI_JUDGE_KEY=jk\n")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(cli_app, ["harness", "check", "fake"])
    assert result.exit_code == 1, result.output
    assert "CLI_AGENT_KEY" in result.output and "credentials.agents" in result.output
    result = runner.invoke(cli_app, ["run", "envcheck", "--agent", "fake"])
    assert result.exit_code == 1, result.output
    assert "CLI_AGENT_KEY" in result.output
    assert "envcheck@admin" not in dataset_refs()

    monkeypatch.setenv("CLI_AGENT_KEY", "client-key")
    result = runner.invoke(cli_app, ["harness", "check", "fake"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "fake"])
    assert result.exit_code == 0, result.output
    own_judge = ["--judge-base-url", "http://judge.test", "--judge-api-key-env", "CLI_JUDGE_KEY"]
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "fake", *own_judge])
    assert result.exit_code == 1, result.output
    assert "CLI_JUDGE_KEY is not set" in result.output
    monkeypatch.setenv("CLI_JUDGE_KEY", "jk")
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "fake", *own_judge])
    assert result.exit_code == 0, result.output


def test_canonical_sync_reuses_bytes_already_uploaded(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    data = b"k,v\n1,2\n"
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / "evalkit" / "fixtures" / "promo.csv").write_bytes(data)
    (tmp_path / "evalkit" / "cases" / "fixtures.yaml").write_text(
        f"fixtures:\n- key: promo.csv\n  sha256: {digest}\n  context: ctx\n"
    )
    promo = tmp_path / "evalkit" / "cases" / "promo"
    promo.mkdir()
    (promo / "p.yaml").write_text(
        "cases:\n- id: promo-1\n  fixtures: [promo.csv]\n  input: {question: 'q 2024-01-01'}\n"
    )
    result = runner.invoke(cli_app, ["sync", "promo"])
    assert result.exit_code == 0, result.output
    (tmp_path / "evalkit" / "fixtures" / "promo.csv").unlink()
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


def test_local_harness_check_reports_an_expired_login_as_one_line(
    env, tmp_path, monkeypatch, harness_url
):
    class ExpiredSource:
        def bearer(self) -> str:
            raise LoginExpired("http://x")

    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        local_run, "oidc", SimpleNamespace(build_auth=lambda conn: EvalkitAuth(ExpiredSource()))
    )
    result = runner.invoke(cli_app, ["harness", "check", "fake"])
    assert result.exit_code == 1
    assert "run `evalkit login --url http://x`" in result.output
    assert "Traceback" not in result.output


def test_local_harness_check_reports_refused_client_credentials_as_one_line(
    env, tmp_path, monkeypatch, harness_url
):
    class RefusedSource:
        def bearer(self) -> str:
            raise ClientCredentialsRefused("invalid_client", "bad secret")

    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        local_run, "oidc", SimpleNamespace(build_auth=lambda conn: EvalkitAuth(RefusedSource()))
    )
    result = runner.invoke(cli_app, ["harness", "check", "fake"])
    assert result.exit_code == 1
    assert "client credentials refused by the identity provider: invalid_client" in result.output
    assert "Traceback" not in result.output


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
    (tmp_path / "evalkit" / "fixtures" / "bs.csv").write_bytes(b"a,b\n1,2\n")
    digest = hashlib.sha256(b"a,b\n1,2\n").hexdigest()
    (tmp_path / "evalkit" / "cases" / "fixtures.yaml").write_text(
        f"fixtures:\n- key: bs.csv\n  sha256: {digest}\n  context: Balance sheet.\n"
    )
    (tmp_path / "evalkit" / "cases" / "finance" / "fx.yaml").write_text(
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


def test_code_driven_agent_input_schema_drives_lint(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    case = tmp_path / "turns.yaml"
    case.write_text("- id: multi\n  input: {turns: [hi, again]}\n")
    created = runner.invoke(cli_app, ["agents", "create", "multiturn"])
    assert created.exit_code == 0, created.output
    assert "default (a required string question)" in created.output
    refused = runner.invoke(cli_app, ["lint", str(case), "--agent", "multiturn"])
    assert refused.exit_code == 1 and "'question' is a required property" in refused.output
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object", "required": ["turns"]}))
    edited = runner.invoke(cli_app, ["agents", "edit", "multiturn", "--input-schema", str(schema)])
    assert edited.exit_code == 0, edited.output
    assert '{"type": "object", "required": ["turns"]}' in edited.output
    passed = runner.invoke(cli_app, ["lint", str(case), "--agent", "multiturn"])
    assert passed.exit_code == 0, passed.output
    not_json = tmp_path / "schema.yaml"
    not_json.write_text("type: object\n")
    bad = runner.invoke(cli_app, ["agents", "edit", "multiturn", "--input-schema", str(not_json)])
    assert bad.exit_code == 2
    kept = runner.invoke(cli_app, ["agents", "show", "multiturn"]).output
    assert '{"type": "object", "required": ["turns"]}' in kept
    cleared = runner.invoke(cli_app, ["agents", "edit", "multiturn", "--clear-input-schema"])
    assert "default (a required string question)" in cleared.output
    harness = runner.invoke(
        cli_app,
        [
            "agents",
            "create",
            "typedharness",
            "--harness-url",
            "http://h",
            "--input-schema",
            str(schema),
        ],
    )
    assert harness.exit_code == 1 and "code-driven" in harness.output


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
        async with tenant_session(factory, "default") as session:
            await record_blob(session, "default", digest, 1, None)
            await session.commit()
        canonical = await WorkspaceStore(factory, "default").get_by_name("canonical")
        admin = {u.username: u.id for u in await UserStore(factory, "default").list()}["admin"]
        doc = CaseFile(key="doc.pdf", sha256=digest, context="c")
        await PgStore(factory, "default").sync(
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
    (tmp_path / "evalkit" / "fixtures" / "bs.csv").write_bytes(data)
    (tmp_path / "evalkit" / "cases" / "fixtures.yaml").write_text(
        f"fixtures:\n- key: bs.csv\n  sha256: {hashlib.sha256(data).hexdigest()}\n  context: c\n"
    )
    listing = tmp_path / "evalkit" / "cases" / "listing"
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
    paths = "cases: evalkit/cases\nfixtures: evalkit/fixtures\n"
    config.write_text(config.read_text().replace(paths, ""))
    monkeypatch.chdir(tmp_path)
    _seed_canonical(module_db_url, "shared")
    args = ["run", "shared@canonical", "--agent", "fake", "--case-id", "shared-1"]
    result = runner.invoke(cli_app, args)
    assert result.exit_code == 0, result.output
    missing = runner.invoke(cli_app, ["sync", "ghost"])
    assert missing.exit_code == 1 and "set `cases:` in evalkit.yaml" in missing.output
    assert str(tmp_path / "evalkit" / "cases") in missing.output.replace("\n", "")
    synced = runner.invoke(cli_app, ["sync", "finance"])
    assert synced.exit_code == 0, synced.output
    own = runner.invoke(cli_app, ["run", "finance", "--agent", "fake"])
    assert own.exit_code == 0, own.output


def test_sync_and_local_run_without_evalkit_yaml(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    (tmp_path / "evalkit.yaml").unlink()
    monkeypatch.chdir(tmp_path)
    missing = runner.invoke(cli_app, ["sync", "ghost"])
    assert missing.exit_code == 1 and "no dataset directory 'ghost'" in missing.output
    synced = runner.invoke(cli_app, ["sync", "finance"])
    assert synced.exit_code == 0, synced.output
    keyless = runner.invoke(cli_app, ["run", "finance", "--agent", "fake"])
    assert "no evalkit.yaml" not in keyless.output
    assert "harness returned 401" in str(keyless.exception)


def test_local_run_with_failed_scoring_says_how_to_finish(env, tmp_path, monkeypatch, harness_url):
    _, application = env
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    real_publish = experiments_api.db_publish_evaluations

    async def publish_failing(session, experiment_run_id, rows, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(experiments_api, "db_publish_evaluations", publish_failing)
    result = runner.invoke(cli_app, ["run", "finance", "--agent", "fake"])
    assert result.exit_code == 1, result.output
    exp = latest_experiment("finance@admin")
    assert exp.status == "failed"
    assert "scoring failed for 1 of 1 runs" in result.output
    assert f"finish it with: evalkit evaluate {exp.id}" in result.output

    monkeypatch.setattr(experiments_api, "db_publish_evaluations", real_publish)
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


def test_run_judge_flags_start_from_the_server_default(
    env, tmp_path, monkeypatch, harness_url, module_db_url
):
    default = api_get("/api/judge-default")
    assert default["model"] == "judge-model"
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    _local_run("finance", "--agent", "fake", "--judge-model", "x", "--judge-effort", "low")
    judge = latest_experiment("finance@admin").config.judge
    assert (judge.model, judge.reasoning_effort, judge.provider) == ("x", "low", "judge")
    no_key = runner.invoke(
        cli_app, ["run", "finance", "--agent", "fake", "--judge-base-url", "http://judge.test"]
    )
    assert no_key.exit_code == 1 and "--judge-api-key-env" in no_key.output
    stray = runner.invoke(
        cli_app, ["run", "finance", "--agent", "fake", "--judge-api-key-env", "CLI_JUDGE_KEY"]
    )
    assert stray.exit_code == 1 and "goes with --judge-base-url" in stray.output
    _local_run(
        "finance",
        "--agent",
        "fake",
        "--judge-base-url",
        "http://judge.test",
        "--judge-api-key-env",
        "CLI_JUDGE_KEY",
    )
    byok = latest_experiment("finance@admin")
    judge = byok.config.judge
    assert judge is not None and judge.api_key is None
    rescore = runner.invoke(cli_app, ["evaluate", str(byok.id)])
    assert rescore.exit_code == 1 and "--judge-api-key-env" in rescore.output
    assert api_get(f"/api/experiments/{byok.id}")["status"] == "completed"
    rescore = runner.invoke(
        cli_app, ["evaluate", str(byok.id), "--judge-api-key-env", "CLI_JUDGE_KEY"]
    )
    assert rescore.exit_code == 0, rescore.output
    engine = create_engine(module_db_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        run_id = conn.execute(
            text("SELECT min(id) FROM experiment_runs WHERE experiment_id = :e"),
            {"e": byok.id},
        ).scalar()
        conn.execute(text("DELETE FROM evaluations WHERE experiment_run_id = :r"), {"r": run_id})
        conn.execute(text("DELETE FROM experiment_runs WHERE id = :r"), {"r": run_id})
        conn.execute(text("UPDATE experiments SET status = 'failed' WHERE id = :e"), {"e": byok.id})
    engine.dispose()
    resume = ["run", "finance", "--agent", "fake", "--resume", str(byok.id)]
    refused = runner.invoke(cli_app, resume)
    assert refused.exit_code == 1 and "--judge-api-key-env" in refused.output
    assert api_get(f"/api/experiments/{byok.id}")["status"] == "failed"
    resumed = runner.invoke(cli_app, [*resume, "--judge-api-key-env", "CLI_JUDGE_KEY"])
    assert resumed.exit_code == 0, resumed.output
    assert (judge.base_url, judge.provider, judge.model) == (
        "http://judge.test",
        None,
        "judge-model",
    )
    both = runner.invoke(
        cli_app,
        ["run", "finance", "--agent", "fake", "--judge-provider", "judge", "--judge-base-url", "u"],
    )
    assert both.exit_code == 1 and "not both" in both.output


def test_a_full_base_url_judge_runs_without_a_default_judge(
    env, tmp_path, monkeypatch, harness_url, module_db_url
):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    engine = create_engine(module_db_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        saved = conn.execute(text("SELECT default_judge FROM tenant_settings")).scalar()
        conn.execute(text("UPDATE tenant_settings SET default_judge = NULL"))
    try:
        _local_run(
            "finance",
            "--agent",
            "fake",
            "--judge-base-url",
            "http://judge.test",
            "--judge-model",
            "m",
            "--judge-api-key-env",
            "CLI_JUDGE_KEY",
        )
        judge = latest_experiment("finance@admin").config.judge
        assert (judge.base_url, judge.model) == ("http://judge.test", "m")
    finally:
        with engine.connect() as conn:
            conn.execute(
                text("UPDATE tenant_settings SET default_judge = CAST(:j AS jsonb)"),
                {"j": json.dumps(saved)},
            )
        engine.dispose()


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
            return httpx2.Response(200, json={"runs": [], "total": 0})

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
            return httpx2.Response(200, content=page(json))

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
        raise httpx2.ConnectError("refused")

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


def test_summary_with_baseline_reads_the_compare_endpoint(monkeypatch):
    def report(exp_id, overall):
        case = {"case_id": "c1", "overall": overall, "scores": {}, "errors": {}, "run_errors": []}
        return {
            "name": f"#{exp_id}",
            "experiment_id": str(exp_id),
            "dataset": "finance@dev",
            "buckets": {},
            "evaluators": [],
            "cases": [case],
            "gauges": [],
        }

    class StubResponse:
        def json(self):
            return {"base": report(3, 0.0), "compare": report(4, 1.0)}

    class StubClient:
        def get(self, path, **kwargs):
            assert path == "/api/experiments/compare"
            assert kwargs["params"] == {"base": 3, "compare": 4}
            return StubResponse()

    monkeypatch.setattr(main_module, "_client", lambda: StubClient())
    result = runner.invoke(cli_app, ["summary", "4", "--baseline", "3"])
    assert result.exit_code == 0, result.output
    assert "vs experiment 3  #3 of finance@dev" in result.output
    assert re.search(r"c1\s+0\.00 → 1\.00  Δ\+1\.00$", result.output, re.M)


def test_offline_lint_refuses_a_file_that_is_not_a_case_file(tmp_path):
    registry = tmp_path / "fixtures.yaml"
    registry.write_text("fixtures: []\n")
    result = runner.invoke(cli_app, ["lint", "--offline", str(registry)])
    assert result.exit_code != 0
    assert "is not a case file" in result.output


def test_offline_lint_reports_schema_errors_as_findings(tmp_path):
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "cases.yaml").write_text(
        "- id: q1\n  input: {question: q}\n  evaluators: {output: {max_response_time: 90}}\n"
    )
    result = runner.invoke(cli_app, ["lint", "--offline", str(tmp_path)])
    assert result.exit_code != 0
    assert "E-SCHEMA [q1] acme/cases.yaml: evaluators.output.max_response_time" in result.output


def test_offline_lint_accepts_x_keys_holding_yaml_anchors(tmp_path):
    (tmp_path / "acme").mkdir()
    (tmp_path / "acme" / "cases.yaml").write_text(
        "x-shared:\n"
        "  question: &question {question: State the NAV}\n"
        "cases:\n"
        "- id: q1\n"
        "  input: *question\n"
    )
    result = runner.invoke(cli_app, ["lint", "--offline", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "E-SCHEMA" not in result.output


def test_users_add_with_a_subject_skips_the_password(env):
    result = runner.invoke(cli_app, ["users", "add", "ci-bot", "--subject", "client-1"])
    assert result.exit_code == 1 and "SSO is not configured" in result.output


def test_users_add_stores_the_username_in_lowercase(env):
    result = runner.invoke(cli_app, ["users", "add", "Bob", "--password", "password"])
    assert result.exit_code == 0, result.output
    assert "created bob" in result.output
    assert "bob" in runner.invoke(cli_app, ["users", "list"]).output


def test_users_list_shows_kind_and_email(env):
    result = runner.invoke(cli_app, ["users", "list"])
    assert result.exit_code == 0
    assert "human" in result.output


def idp_providers(idp: FakeIdp, scopes: list[str]) -> dict:
    return {
        "password": "admin",
        "oidc": {
            **idp.discovery(),
            "name": "Fake",
            "client_id": idp.client_id,
            "cli_client_id": idp.client_id,
            "audience": idp.audience,
            "scopes": scopes,
            "authorize_params": {},
        },
    }


REGISTERED = {
    "username": "ann",
    "is_admin": False,
    "email": "ann@example.com",
    "kind": "human",
    "created": True,
}


def test_login_falls_back_to_the_password_path_without_an_idp(server, tmp_path, monkeypatch):
    base_url, _ = server
    config_path = tmp_path / "login.toml"
    monkeypatch.setattr(connection, "CONFIG_PATH", config_path)
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", base_url], input="admin\nhunter2-pw\n")
    assert result.exit_code == 0, result.output
    assert "token = " in config_path.read_text()


def test_login_without_url_names_hosted_evalkit_and_never_prompts(tmp_path, monkeypatch):
    asked: list[str] = []

    def fetch(url, transport=None):
        asked.append(url)
        return {"oidc": None}

    posted: list[str] = []

    class StubClient:
        def __init__(self, url):
            posted.append(url)

        def post(self, path, json):
            return httpx2.Response(200, json={"token": "tok", "username": "ann"})

    monkeypatch.setattr(client_oidc, "fetch_providers", fetch)
    monkeypatch.setattr(main_module, "ApiClient", StubClient)
    config_path = tmp_path / "login.toml"
    monkeypatch.setattr(connection, "CONFIG_PATH", config_path)
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login"], input="ann\npw\n")
    assert result.exit_code == 0, result.output
    first_line = result.output.splitlines()[0]
    assert first_line == (
        "logging in to https://evalkit.inferedge.com; pass --url to log in to another server"
    )
    assert "Server URL" not in result.output
    assert asked == posted == [connection.DEFAULT_SERVER_URL]
    saved = connection.load(config_path)
    assert saved is not None and saved.url == "https://evalkit.inferedge.com"


def test_login_url_overrides_hosted_evalkit(tmp_path, monkeypatch):
    asked: list[str] = []

    def fetch(url, transport=None):
        asked.append(url)
        raise httpx2.ConnectError("stop here")

    monkeypatch.setattr(client_oidc, "fetch_providers", fetch)
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "login.toml")
    monkeypatch.setenv("EVALKIT_SERVER_URL", "https://env.test")
    result = runner.invoke(cli_app, ["login", "--url", "https://evalkit.test"])
    assert asked == ["https://evalkit.test"]
    assert "logging in to" not in result.output and "inferedge.com" not in result.output


def test_login_runs_the_loopback_flow_and_registers(tmp_path, monkeypatch):
    idp = FakeIdp()
    idp.next_login = ("sub-ann", "ann@example.com", "ann")
    providers = idp_providers(idp, ["openid", "email", "profile", "offline_access"])
    monkeypatch.setattr(client_oidc, "fetch_providers", lambda url, transport=None: providers)
    monkeypatch.setattr(
        client_oidc, "register_identity", lambda url, tokens, transport=None: REGISTERED
    )
    monkeypatch.setattr(
        client_oidc,
        "run_loopback_flow",
        lambda oidc_cfg, open_browser, **kw: idp._issue(idp._login()),
    )
    config_path = tmp_path / "login.toml"
    monkeypatch.setattr(connection, "CONFIG_PATH", config_path)
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", "https://evalkit.test"])
    assert result.exit_code == 0, result.output
    assert "logged in as ann on https://evalkit.test" in result.output
    saved = connection.load(config_path)
    assert saved is not None
    assert saved.username == "ann" and saved.oidc is not None and saved.token is None


def test_login_reports_a_refused_code_exchange_as_one_line(tmp_path, monkeypatch):
    idp = FakeIdp()
    providers = idp_providers(idp, ["openid", "email", "profile", "offline_access"])
    monkeypatch.setattr(client_oidc, "fetch_providers", lambda url, transport=None: providers)

    def refused(oidc_cfg, open_browser, **kw):
        raise client_oidc.LoginRefused("invalid_grant: code expired")

    monkeypatch.setattr(client_oidc, "run_loopback_flow", refused)
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "login.toml")
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", "https://evalkit.test"])
    assert result.exit_code == 1
    assert "invalid_grant" in result.output and "Traceback" not in result.output


def test_login_no_browser_uses_the_device_flow(tmp_path, monkeypatch):
    idp = FakeIdp()
    idp.next_login = ("sub-ann", "ann@example.com", "ann")
    providers = idp_providers(idp, ["openid"])
    called: list[str] = []

    def device(oidc_cfg, echo, **kw):
        called.append("device")
        return idp._issue(idp._login())

    monkeypatch.setattr(client_oidc, "fetch_providers", lambda url, transport=None: providers)
    monkeypatch.setattr(
        client_oidc, "register_identity", lambda url, tokens, transport=None: REGISTERED
    )
    monkeypatch.setattr(client_oidc, "run_device_flow", device)
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "login.toml")
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", "https://evalkit.test", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert called == ["device"]


def test_login_reports_a_refused_login_as_one_line(tmp_path, monkeypatch):
    idp = FakeIdp()
    providers = idp_providers(idp, ["openid"])

    def refuse(oidc_cfg, open_browser, **kw):
        raise client_oidc.LoginRefused("access_denied: no")

    monkeypatch.setattr(client_oidc, "fetch_providers", lambda url, transport=None: providers)
    monkeypatch.setattr(client_oidc, "run_loopback_flow", refuse)
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "login.toml")
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", "https://evalkit.test"])
    assert result.exit_code == 1
    assert "access_denied: no" in result.output
    assert not (tmp_path / "login.toml").exists()


def test_login_refuses_tokens_without_a_refresh_token(tmp_path, monkeypatch):
    idp = FakeIdp()
    idp.next_login = ("sub-ann", "ann@example.com", "ann")
    providers = idp_providers(idp, ["openid"])
    tokens = {k: v for k, v in idp._issue(idp._login()).items() if k != "refresh_token"}
    monkeypatch.setattr(client_oidc, "fetch_providers", lambda url, transport=None: providers)
    monkeypatch.setattr(
        client_oidc, "register_identity", lambda url, tokens, transport=None: REGISTERED
    )
    monkeypatch.setattr(
        client_oidc, "run_loopback_flow", lambda oidc_cfg, open_browser, **kw: tokens
    )
    monkeypatch.setattr(connection, "CONFIG_PATH", tmp_path / "login.toml")
    monkeypatch.delenv("EVALKIT_SERVER_URL", raising=False)
    result = runner.invoke(cli_app, ["login", "--url", "https://evalkit.test"])
    assert result.exit_code == 1
    assert "issued no refresh token" in result.output and "offline_access" in result.output
    assert not (tmp_path / "login.toml").exists()


def test_whoami_names_the_login_kind(env):
    result = runner.invoke(cli_app, ["whoami"])
    assert result.exit_code == 0 and "login: password" in result.output


def test_half_of_the_client_credentials_is_a_one_line_error(monkeypatch):
    monkeypatch.setenv("EVALKIT_SERVER_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("EVALKIT_OIDC_CLIENT_ID", "ci")
    monkeypatch.delenv("EVALKIT_OIDC_CLIENT_SECRET", raising=False)
    result = runner.invoke(cli_app, ["datasets", "list"])
    assert result.exit_code == 1
    assert "EVALKIT_OIDC_CLIENT_SECRET" in result.output
    assert "Traceback" not in result.output


def test_experiments_list_filters_by_dataset(env):
    listed = runner.invoke(cli_app, ["experiments", "list", "--dataset", "finance@canonical"])
    assert listed.exit_code == 0, listed.output
    rows = api_get("/api/experiments", dataset="finance@canonical")
    if rows:
        header, first = listed.output.splitlines()[:2]
        assert header.split()[:2] == ["id", "dataset"]
        assert first.split()[0] == str(rows[0]["id"])
    else:
        assert "no experiments" in listed.output
    missing = runner.invoke(cli_app, ["experiments", "list", "--dataset", "nope@canonical"])
    assert missing.exit_code == 1


def test_review_show_yaml_drops_filled_in_defaults():
    stored = {
        "output": {
            "answer_rubrics": {
                "rubrics": [{"rubric": "Net debt is $1.2B", "type": "derived_computation"}]
            },
            "max_response_time": {"max_seconds": 120.0},
        },
        "trajectory": {
            "tool_trajectory_match": {"expected": [{"name": "search"}], "ordered": False}
        },
    }
    full = main_module.CaseEvaluators.model_validate(stored).as_metadata()
    shown = main_module._authored_evaluators(full)
    assert "source" not in str(shown) and "args_mode" not in str(shown)
    assert shown["trajectory"] == {"tool_trajectory_match": {"expected": [{"name": "search"}]}}
    assert main_module._authored_evaluators({}) is None


def test_trace_source_flags_go_together_and_imports_default_to_the_agent(
    env, tmp_path, monkeypatch, harness_url
):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    lone = runner.invoke(cli_app, ["agents", "edit", "sdk", "--trace-source", "phx"])
    assert lone.exit_code == 1 and "trace_source and project go together" in lone.output
    both = runner.invoke(
        cli_app, ["agents", "edit", "sdk", "--clear-trace-source", "--project", "ra"]
    )
    assert both.exit_code == 1 and "--clear-trace-source" in both.output
    unknown = runner.invoke(
        cli_app, ["agents", "edit", "sdk", "--trace-source", "nope", "--project", "ra"]
    )
    assert unknown.exit_code == 1 and "trace source nope not found" in unknown.output
    imported = runner.invoke(cli_app, ["traces", "import", "sdk", "s-1"])
    assert imported.exit_code == 1
    assert "no trace source given and agent sdk@admin has none" in imported.output
