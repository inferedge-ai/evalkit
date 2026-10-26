from pathlib import Path

from tests.cli.test_cli import (  # noqa: F401
    _exp_id,
    _serve,
    api_get,
    env,
    experiment_runs,
    fake_harness,
    harness_url,
    in_db,
    latest_experiment,
    runner,
    server,
    stub_judges_module,
    write_local_config,
)

LOCAL_CASE = (
    "- id: {id}\n  tags: [{tag}]\n  input: {{question: 'q {id}'}}\n"
    "  evaluators:\n    output:\n      answer_rubrics:\n        rubrics:\n"
    "        - rubric: 'Says 42.'\n          type: factual_retrieval\n"
)


def write_cases(root: Path, dataset: str, *case_ids: str, tag: str = "local") -> Path:
    directory = root / "eval" / "cases" / dataset
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "cases.yaml").write_text(
        "cases:\n" + "".join(LOCAL_CASE.format(id=case_id, tag=tag) for case_id in case_ids)
    )
    return directory
