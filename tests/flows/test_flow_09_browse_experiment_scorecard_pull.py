import json
import os
import time

import httpx

from evalkit.cli.main import app as cli_app
from tests.flows.helpers_07_11 import (
    _exp_id,
    api_get,
    env,
    experiment_runs,
    fake_harness,
    harness_url,
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


def api(method: str, path: str, **kwargs) -> httpx.Response:
    return httpx.request(
        method,
        f"{os.environ['EVALKIT_SERVER_URL']}{path}",
        headers={"Authorization": f"Bearer {os.environ['EVALKIT_TOKEN']}"},
        **kwargs,
    )


def fmt(value: float | None) -> str:
    return f"{value:.2f}" if value is not None else "  - "


def server_run(tmp_path, monkeypatch, harness_url, dataset: str) -> int:
    write_local_config(tmp_path, harness_url)
    directory = write_cases(tmp_path, dataset, f"{dataset}-1", f"{dataset}-2")
    text = (directory / "cases.yaml").read_text()
    first, _, rest = text.partition(f"- id: {dataset}-2")
    first = first.replace(
        "          type: factual_retrieval\n",
        "          type: factual_retrieval\n        - rubric: 'Names the filing.'\n"
        "          type: factual_retrieval\n",
    )
    (directory / "cases.yaml").write_text(f"{first}- id: {dataset}-2{rest}")
    monkeypatch.chdir(tmp_path)
    run_cli("sync", dataset, "--workspace", "canonical")
    for case_id in (f"{dataset}-1", f"{dataset}-2"):
        run_cli("review", "approve", f"{dataset}@canonical", case_id)
    return _exp_id(run_cli("run", f"{dataset}@canonical", "--agent", "fake@canonical").output)


def wait_done(exp_id: int) -> dict:
    for _ in range(200):
        body = api_get(f"/api/experiments/{exp_id}")
        if body["status"] in ("completed", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(body["status"])


def test_flow_09_experiment_list_page_scorecard_and_summary_agree(
    env, tmp_path, monkeypatch, harness_url
):
    exp_id = server_run(tmp_path, monkeypatch, harness_url, "flow09")
    rows = api_get("/api/datasets/flow09@canonical/experiments")
    [row] = [r for r in rows if r["id"] == exp_id]
    assert isinstance(row["id"], int) and row["number"] >= 1
    report = api_get(f"/api/experiments/{exp_id}/scorecard")
    assert row["scores"] == {b: v for b, v in report["buckets"].items() if v is not None}

    body = api_get(f"/api/experiments/{exp_id}")
    assert body["status"] == "completed" and body["status_error"] is None
    assert body["workspace"] == "canonical"
    assert body["progress"] == {"total": 2, "ran": 2, "evaluated": 2}
    assert body["case_states"] == {"flow09-1": "evaluated", "flow09-2": "evaluated"}
    assert body["can_change"] is True and body["server_may_drive"] is True
    assert body["config"]["snapshot"]["evalkit_version"]

    summary = run_cli("summary", str(exp_id)).output
    for bucket, value in report["buckets"].items():
        if value is None:
            assert f"  {bucket:<14}" not in summary
        else:
            assert f"  {bucket:<14} {fmt(value)}" in summary
    for stat in report["evaluators"]:
        assert f"  {stat['name']:<40} {fmt(stat['score'])}" in summary

    history = api_get("/api/datasets/flow09@canonical/cases/flow09-1/scores")
    [point] = [p for p in history["points"] if p["experiment_id"] == exp_id]
    assert point["number"] == row["number"]
    assert set(point["scores"]) and "cost_usd" in point


def test_flow_09_run_page_trajectory_files_and_pinned_case(env, tmp_path, monkeypatch, harness_url):
    exp_id = server_run(tmp_path, monkeypatch, harness_url, "flow09p")
    run = next(r for r in experiment_runs(exp_id) if r.case_id == "flow09p-1")
    trajectory = api_get(f"/api/experiments/{exp_id}/runs/{run.experiment_run_id}/trajectory")
    assert trajectory["steps"]
    files = api_get(f"/api/agents/fake@canonical/runs/{run.agent_run.harness_run_id}/files")
    assert "outputs/report.md" in {f["path"] for f in files["files"]}
    zipped = api("GET", f"/api/agents/fake@canonical/runs/{run.agent_run.harness_run_id}/zip")
    assert zipped.status_code == 200 and zipped.content[:2] == b"PK"

    pinned = {c["case_id"]: c["content_hash"] for c in api_get(f"/api/experiments/{exp_id}/cases")}
    cases_yaml = tmp_path / "eval" / "cases" / "flow09p" / "cases.yaml"
    cases_yaml.write_text(cases_yaml.read_text().replace("q flow09p-1", "q flow09p-1 edited"))
    run_cli("sync", "flow09p", "--workspace", "canonical")
    current = {
        c["case_id"]: c["content_hash"] for c in api_get("/api/datasets/flow09p@canonical/cases")
    }
    assert current["flow09p-1"] != pinned["flow09p-1"]
    again = {c["case_id"]: c["content_hash"] for c in api_get(f"/api/experiments/{exp_id}/cases")}
    assert again == pinned


def test_flow_09_runs_pull_brings_every_run_with_scores_to_select_failures(
    env, tmp_path, monkeypatch, harness_url
):
    exp_id = server_run(tmp_path, monkeypatch, harness_url, "flow09c")
    report = api_get(f"/api/experiments/{exp_id}/scorecard")
    failing = {
        c["case_id"] for c in report["cases"] if c["overall"] is not None and c["overall"] < 0.8
    }
    assert failing == {"flow09c-1"}

    out = tmp_path / "pulled"
    run_cli("runs", "pull", "--experiment", str(exp_id), "--out", str(out))
    index = json.loads((out / "index.json").read_text())
    assert {row["case_id"] for row in index["runs"]} == {"flow09c-1", "flow09c-2"}
    selected = {
        row["case_id"]
        for row in index["runs"]
        if row["status"] != "completed" or any(v < 0.8 for v in row["scores"].values())
    }
    assert selected == failing
    row = next(r for r in index["runs"] if r["case_id"] == "flow09c-1")
    run = json.loads((out / row["dir"] / "run.json").read_text())
    assert {"status", "answer", "metrics"} <= set(run)
    assert json.loads((out / row["dir"] / "evaluations.json").read_text())
    assert (out / row["dir"] / "trajectory.json").is_file()


def test_flow_09_re_evaluate_resume_rules_and_404s(env, tmp_path, monkeypatch, harness_url):
    exp_id = server_run(tmp_path, monkeypatch, harness_url, "flow09r")
    evaluated = api("POST", f"/api/experiments/{exp_id}/evaluate")
    assert evaluated.status_code == 200, evaluated.text
    assert wait_done(exp_id)["status"] == "completed"
    resumed = api("POST", f"/api/experiments/{exp_id}/resume")
    assert resumed.status_code == 409

    assert api("GET", "/api/datasets/nope@canonical").status_code == 404
    assert api("GET", "/api/experiments/999999").status_code == 404
    missing = api("GET", f"/api/experiments/{exp_id}/runs/999999/trajectory")
    assert missing.status_code == 404 and missing.json()["detail"] == "unknown run"
