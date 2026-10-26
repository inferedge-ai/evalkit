import os
from collections.abc import Iterator

import pytest

from evalkit.cli import main as cli_main
from evalkit.cli import skill_targets
from evalkit.client import api as client_api
from evalkit.core.evaluators import presentation, rubrics, trajectory_rubrics
from evalkit.core.judge import JudgeUsage
from evalkit.db import users

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

pytest_plugins = ["tests.pg"]


@pytest.fixture(scope="session", autouse=True)
def fast_waits() -> Iterator[None]:
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(users, "BCRYPT_ROUNDS", 4)
        mp.setattr(cli_main, "RUN_POLL_SECONDS", 0.02)
        mp.setattr(client_api, "RETRY_BACKOFF_FACTOR", 0.0)
        yield


@pytest.fixture(autouse=True)
def isolated_skill_targets(tmp_path_factory, monkeypatch):
    path = tmp_path_factory.mktemp("home") / "skills-targets"
    monkeypatch.setattr(skill_targets, "TARGETS_PATH", path)
    return path


@pytest.fixture
def stub_judges(monkeypatch):
    async def fake_rubric_judge(cfg, **kwargs):
        return (
            rubrics.RubricJudgeOutput(
                verdicts=[
                    rubrics.RubricVerdict(index=1, verdict="satisfied", source=None),
                    rubrics.RubricVerdict(
                        index=2, verdict="incorrect", note="wrong figure", source=None
                    ),
                ]
            ),
            JudgeUsage(input_tokens=100, output_tokens=10),
        )

    monkeypatch.setattr(rubrics, "call_judge", fake_rubric_judge)
    monkeypatch.setattr(presentation, "call_judge", fake_rubric_judge)
    monkeypatch.setattr(trajectory_rubrics, "call_judge", fake_rubric_judge)
