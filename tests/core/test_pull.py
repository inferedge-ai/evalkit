import json
from datetime import UTC, datetime

from evalkit.contracts.enums import EvaluationSource, EvaluatorKind, RunStatus
from evalkit.core.pull import (
    RunBundle,
    bundle_files,
    index_row,
    merge_index,
    run_body,
    safe_dir,
)
from evalkit.store.base import AgentRunRecord, CaseView, EvaluationRow, RunEvalContext

STARTED = datetime(2026, 10, 5, tzinfo=UTC)


def evaluation(name: str, value: float | None, **extra) -> EvaluationRow:
    return EvaluationRow(
        producer=name,
        name=name,
        kind=extra.pop("kind", EvaluatorKind.SCORE),
        source=EvaluationSource.BUILTIN,
        value=value,
        **extra,
    )


def eval_bundle() -> RunBundle:
    context = RunEvalContext(
        experiment_id=7,
        repetition=1,
        case=CaseView(case_ref=1, case_id="case-1", content_hash="abc", input={"question": "q"}),
        evaluations=[
            evaluation("answer_rubrics", 0.5),
            evaluation("tokens", 120.0, kind=EvaluatorKind.GAUGE),
            evaluation("presentation_judge", None, error="judge down"),
        ],
    )
    record = AgentRunRecord(harness_run_id="r/1", status=RunStatus.COMPLETED, started_at=STARTED)
    return RunBundle(
        run_id="r/1",
        source="eval",
        status="completed",
        created_at=STARTED,
        run=run_body("r/1", "eval", record, context),
        trajectory={"steps": []},
        eval=context,
    )


def test_safe_dir_replaces_unsafe_characters():
    assert safe_dir("a/b:c d") == "a_b_c_d"
    assert safe_dir("sess-1.2_x") == "sess-1.2_x"


def test_eval_bundle_files_and_index_row():
    bundle = eval_bundle()
    files = bundle_files(bundle)
    assert sorted(files) == [
        "runs/r_1/case.json",
        "runs/r_1/evaluations.json",
        "runs/r_1/run.json",
        "runs/r_1/trajectory.json",
    ]
    assert json.loads(files["runs/r_1/case.json"])["content_hash"] == "abc"
    assert len(json.loads(files["runs/r_1/evaluations.json"])) == 3
    run = json.loads(files["runs/r_1/run.json"])
    assert run["input"] == {"question": "q"} and run["status"] == "completed"
    row = index_row(bundle)
    assert row["run_id"] == "r/1" and row["dir"] == "runs/r_1"
    assert row["scores"] == {"answer_rubrics": 0.5}
    assert row["case_id"] == "case-1" and row["experiment_id"] == 7


def test_app_bundle_with_error_has_no_trajectory_or_case():
    bundle = RunBundle(
        run_id="s-1",
        source="app",
        run={"run_id": "s-1", "source": "app"},
        pull_error="harness unreachable",
    )
    files = bundle_files(bundle)
    assert sorted(files) == ["runs/s-1/run.json"]
    assert json.loads(files["runs/s-1/run.json"])["pull_error"] == "harness unreachable"
    row = index_row(bundle)
    assert row["scores"] is None and row["error"] is None
    assert row["pull_error"] == "harness unreachable"


def test_merge_index_keeps_old_runs_and_takes_new_filters():
    old = {
        "filters": {"offset": 0},
        "total_matching": 3,
        "runs": [{"run_id": "a"}, {"run_id": "b"}],
    }
    new = {
        "filters": {"offset": 2},
        "total_matching": 4,
        "runs": [{"run_id": "b", "status": "x"}, {"run_id": "c"}],
    }
    merged = merge_index(old, new)
    assert merged["filters"] == {"offset": 2} and merged["total_matching"] == 4
    assert merged["runs"] == [{"run_id": "a"}, {"run_id": "b", "status": "x"}, {"run_id": "c"}]
    assert merge_index(None, new) == new
