import hashlib
import os

import httpx

from evalkit.cli import main as main_module
from evalkit.cli.main import app as cli_app
from tests.fake_harness import FakeHarness
from tests.flows.helpers_07_11 import (
    _exp_id,
    _serve,
    api_get,
    env,
    experiment_runs,
    fake_harness,
    harness_url,
    latest_experiment,
    runner,
    server,
    stub_judges_module,
    write_cases,
    write_local_config,
)

__all__ = ["env", "fake_harness", "harness_url", "server", "stub_judges_module"]


def run_cli(*args: str, code: int = 0):
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == code, result.output
    return result


def api_post(path: str) -> httpx.Response:
    return httpx.post(
        f"{os.environ['EVALKIT_SERVER_URL']}{path}",
        headers={"Authorization": f"Bearer {os.environ['EVALKIT_TOKEN']}"},
    )


def test_flow_07_local_run_syncs_drives_scores_and_lands_in_my_workspace(
    env, tmp_path, monkeypatch, harness_url
):
    write_local_config(tmp_path, harness_url)
    write_cases(tmp_path, "flow07", "f07-a", "f07-b")
    monkeypatch.chdir(tmp_path)
    result = run_cli("run", "flow07", "--agent", "fake", "--runs", "2", "--model", "gpt-x")
    lines = result.output.splitlines()
    exp_id = _exp_id(result.output)
    assert any(line.startswith(f"experiment {exp_id}") for line in lines)
    for case_id in ("f07-a", "f07-b"):
        for rep in (1, 2):
            assert f"run {case_id} #{rep} completed" in result.output
            assert f"scored {case_id} #{rep}" in result.output
    assert "buckets:" in result.output

    dataset = api_get("/api/datasets/flow07@admin")
    assert (dataset["workspace"], dataset["kind"]) == ("admin", "suite")
    exp = latest_experiment("flow07@admin")
    assert exp.id == exp_id and exp.status == "completed" and exp.status_error is None
    assert (exp.workspace, exp.agent, exp.repetitions) == ("admin", "fake@admin", 2)
    assert exp.config.model == "gpt-x" and exp.lease_holder is None
    body = api_get(f"/api/experiments/{exp_id}")
    assert body["progress"] == {"total": 2, "ran": 2, "evaluated": 2}

    runs = experiment_runs(exp_id)
    assert sorted((r.case_id, r.repetition) for r in runs) == [
        ("f07-a", 1),
        ("f07-a", 2),
        ("f07-b", 1),
        ("f07-b", 2),
    ]
    listed = {r["run_id"] for r in api_get("/api/agents/fake@admin/runs")["runs"]}
    assert {r.agent_run.harness_run_id for r in runs} <= listed

    status = run_cli("status", str(exp_id))
    assert f"experiment {exp_id}" in status.output
    assert "status=completed 2/2 ran, 2/2 evaluated" in status.output

    one = _exp_id(run_cli("run", "flow07", "--agent", "fake", "--case-id", "f07-b").output)
    assert [(r.case_id, r.repetition) for r in experiment_runs(one)] == [("f07-b", 1)]


def test_flow_07_shared_dataset_runs_every_case(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    write_cases(tmp_path, "flow07s", "s-1", "s-2")
    monkeypatch.chdir(tmp_path)
    run_cli("sync", "flow07s", "--workspace", "canonical")
    run_cli("review", "approve", "flow07s@canonical", "s-1")
    exp_id = _exp_id(run_cli("run", "flow07s@canonical", "--agent", "fake").output)
    assert {r.case_id for r in experiment_runs(exp_id)} == {"s-1", "s-2"}
    exp = api_get(f"/api/experiments/{exp_id}")
    assert exp["workspace"] == "admin" and exp["dataset"] == "flow07s@canonical"


def test_flow_07_ctrl_c_marks_failed_and_resume_runs_only_pending(
    env, tmp_path, monkeypatch, harness_url, fake_harness
):
    write_local_config(tmp_path, harness_url)
    write_cases(tmp_path, "flow07r", "r-1", "r-2", "r-3")
    monkeypatch.chdir(tmp_path)
    real = main_module.run_local

    async def interrupted(*args, progress, **kwargs):
        def tripwire(line: str) -> None:
            progress(line)
            if line.startswith("run "):
                raise KeyboardInterrupt

        return await real(*args, progress=tripwire, **kwargs)

    monkeypatch.setattr(main_module, "run_local", interrupted)
    result = run_cli("run", "flow07r", "--agent", "fake", code=130)
    assert "interrupted" in result.output
    exp_id = _exp_id(result.output)
    exp = api_get(f"/api/experiments/{exp_id}")
    assert exp["status"] == "failed"
    assert exp["status_error"] == "interrupted: cancelled by admin"
    done = {
        r.case_id: r.agent_run.harness_run_id
        for r in experiment_runs(exp_id)
        if r.agent_run.status == "completed"
    }
    assert done

    monkeypatch.setattr(main_module, "run_local", real)
    run_cli("run", "flow07r", "--agent", "fake", "--resume", str(exp_id))
    exp = api_get(f"/api/experiments/{exp_id}")
    assert exp["status"] == "completed" and exp["status_error"] is None
    runs = experiment_runs(exp_id)
    assert sorted(r.case_id for r in runs) == ["r-1", "r-2", "r-3"]
    assert all(r.agent_run.status == "completed" for r in runs)
    after = {r.case_id: r.agent_run.harness_run_id for r in runs}
    for case_id, harness_run_id in done.items():
        assert after[case_id] == harness_run_id
        asked = [
            p
            for p in fake_harness.received_payloads.values()
            if p["input"]["question"] == f"q {case_id}"
        ]
        assert len(asked) == 1
    scorecard = api_get(f"/api/experiments/{exp_id}/scorecard")
    assert all(not case["run_errors"] for case in scorecard["cases"])


def test_flow_07_evaluate_is_safe_to_repeat_and_server_resume_points_at_the_cli(
    env, tmp_path, monkeypatch, harness_url
):
    write_local_config(tmp_path, harness_url)
    write_cases(tmp_path, "flow07e", "e-1")
    monkeypatch.chdir(tmp_path)
    exp_id = _exp_id(run_cli("run", "flow07e", "--agent", "fake").output)
    before = {(e.name, e.value) for e in experiment_runs(exp_id)[0].evaluations}
    for _ in range(2):
        run_cli("evaluate", str(exp_id))
        after = {(e.name, e.value) for e in experiment_runs(exp_id)[0].evaluations}
        assert after == before
        assert api_get(f"/api/experiments/{exp_id}")["status"] == "completed"

    resumed = api_post(f"/api/experiments/{exp_id}/resume")
    assert resumed.status_code == 409
    assert resumed.json()["detail"] == (
        f"resume it with evalkit run flow07e@admin --agent fake@admin --resume {exp_id}"
    )
    evaluated = api_post(f"/api/experiments/{exp_id}/evaluate")
    assert evaluated.status_code == 409
    assert evaluated.json()["detail"] == f"evaluate it with evalkit evaluate {exp_id}"


def test_flow_07_harness_without_file_support_drops_fixture_cases(env, tmp_path, monkeypatch):
    nofiles = FakeHarness(capabilities_overrides={"accepts": {"files": False, "file_urls": False}})
    uv_server, thread, port = _serve(nofiles.app)
    try:
        url = f"http://127.0.0.1:{port}/evalkit/v1"
        write_local_config(tmp_path, url)
        config = tmp_path / "evalkit.yaml"
        config.write_text(
            config.read_text().replace(
                "    fake: env:CLI_AGENT_KEY\n",
                "    fake: env:CLI_AGENT_KEY\n    nofiles: env:CLI_AGENT_KEY\n",
            )
        )
        monkeypatch.chdir(tmp_path)
        run_cli("agents", "create", "nofiles", "--harness-url", url)
        data = b"a,b\n1,2\n"
        digest = hashlib.sha256(data).hexdigest()
        (tmp_path / "eval" / "fixtures" / "nf.csv").write_bytes(data)
        (tmp_path / "eval" / "cases" / "fixtures.yaml").write_text(
            f"fixtures:\n- key: nf.csv\n  sha256: {digest}\n  context: ctx\n"
        )
        directory = write_cases(tmp_path, "flow07f", "plain-1")
        (directory / "fx.yaml").write_text(
            "cases:\n- id: file-1\n  fixtures: [nf.csv]\n  input: {question: 'q file-1'}\n"
        )
        output = run_cli("run", "flow07f", "--agent", "nofiles").output
        assert "skipped 1 case(s) with fixture files" in output and "file-1" in output
        exp_id = _exp_id(output)
        body = api_get(f"/api/experiments/{exp_id}")
        assert body["status"] == "completed"
        assert body["progress"]["total"] == 1
        assert [r.case_id for r in experiment_runs(exp_id)] == ["plain-1"]
        assert nofiles.received_files == {}
    finally:
        uv_server.should_exit = True
        thread.join(timeout=5)


def test_flow_07_code_driven_agent_is_refused(env, tmp_path, monkeypatch, harness_url):
    write_local_config(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    result = run_cli("run", "finance", "--agent", "sdk", code=1)
    assert "sdk@admin is a code-driven agent (no harness_url)" in result.output
    assert "evalkit.sdk.run_experiment" in result.output
