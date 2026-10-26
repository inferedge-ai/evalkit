import os

import httpx

from evalkit.cli.main import app as cli_app
from evalkit.core.evaluators import rubrics
from evalkit.core.judge import JudgeUsage
from tests.flows.helpers_07_11 import (
    _exp_id,
    api_get,
    env,
    fake_harness,
    harness_url,
    runner,
    server,
    stub_judges_module,
    write_cases,
    write_local_config,
)

__all__ = ["env", "fake_harness", "harness_url", "server", "stub_judges_module"]

CASE = (
    "- id: {id}\n  input: {{question: 'q {id}'}}\n"
    "  evaluators:\n    output:\n      answer_rubrics:\n        rubrics:\n{rubrics}"
)
RUBRIC = "        - rubric: '{text}'\n          type: factual_retrieval\n"
TWO = RUBRIC.format(text="Says 42.") + RUBRIC.format(text="Names the filing.")
ONE = RUBRIC.format(text="Says 42.")


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


async def all_satisfied(cfg, **kwargs):
    return (
        rubrics.RubricJudgeOutput(
            verdicts=[
                rubrics.RubricVerdict(index=i, verdict="satisfied", source=None) for i in (1, 2)
            ]
        ),
        JudgeUsage(),
    )


def seed_dataset(tmp_path, harness_url) -> None:
    write_local_config(tmp_path, harness_url)
    directory = write_cases(tmp_path, "flow10", "unused")
    (directory / "cases.yaml").write_text(
        "cases:\n"
        + CASE.format(id="c10-1", rubrics=TWO)
        + CASE.format(id="c10-2", rubrics=TWO)
        + CASE.format(id="c10-3", rubrics=ONE)
    )


def test_flow_10_compare_two_experiments_and_summary_baseline(
    env, tmp_path, monkeypatch, harness_url
):
    seed_dataset(tmp_path, harness_url)
    monkeypatch.chdir(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(rubrics, "call_judge", all_satisfied)
        base = _exp_id(
            run_cli(
                "run", "flow10", "--agent", "fake", "--case-id", "c10-1", "--case-id", "c10-3"
            ).output
        )
    compared = _exp_id(run_cli("run", "flow10", "--agent", "fake").output)

    body = api_get("/api/experiments/compare", base=base, compare=compared)
    assert body["base"]["experiment_id"] == str(base)
    report = body["compare"]
    assert report["experiment_id"] == str(compared) and report["baseline_id"] == str(base)
    cases = {c["case_id"]: c for c in report["cases"]}
    assert set(cases) == {"c10-1", "c10-2", "c10-3"}
    assert cases["c10-1"]["baseline_overall"] == 1.0 and cases["c10-1"]["delta"] == -0.5
    assert cases["c10-2"]["baseline_overall"] is None and cases["c10-2"]["delta"] is None
    assert cases["c10-3"]["delta"] == 0.0

    with_baseline = api_get(f"/api/experiments/{compared}/scorecard", baseline_id=base)
    assert with_baseline == report

    reverse = api_get("/api/experiments/compare", base=compared, compare=base)
    assert {c["case_id"] for c in reverse["compare"]["cases"]} == {"c10-1", "c10-3"}

    summary = run_cli("summary", str(compared), "--baseline", str(base)).output
    failing = summary.split("failing cases (< 0.80):")[1].split("\n\n")[0]
    lines = {line.split()[0]: line for line in failing.splitlines() if line.strip()[:4] == "c10-"}
    assert "Δ-0.50" in lines["c10-1"]
    assert "Δ" not in lines["c10-2"]
    assert "c10-3" not in lines
    assert "Δ" not in run_cli("summary", str(compared)).output


def test_flow_10_compare_across_datasets_unknown_ids_and_no_stored_baseline(
    env, tmp_path, monkeypatch, harness_url
):
    write_local_config(tmp_path, harness_url)
    write_cases(tmp_path, "flow10a", "a-1")
    write_cases(tmp_path, "flow10b", "b-1")
    monkeypatch.chdir(tmp_path)
    first = _exp_id(run_cli("run", "flow10a", "--agent", "fake").output)
    second = _exp_id(run_cli("run", "flow10b", "--agent", "fake").output)
    body = api_get("/api/experiments/compare", base=first, compare=second)
    assert [c["delta"] for c in body["compare"]["cases"]] == [None]

    assert (
        api(
            "GET", "/api/experiments/compare", params={"base": first, "compare": 999999}
        ).status_code
        == 404
    )
    assert api(
        "POST", "/api/datasets/flow10a@admin/baseline", json={"experiment_id": first}
    ).status_code in (404, 405)
