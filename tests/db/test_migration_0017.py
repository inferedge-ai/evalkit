import json

import pytest

from evalkit.db.session import downgrade_migrations, run_migrations
from tests.db.test_migration_0016 import _exec, _rows

URL = "https://llm.example/v1"


def _experiment(exp_id: int, agent_id: int, judge: dict) -> str:
    config = json.dumps(
        {
            "judge": judge,
            "selection": {"all": True},
            "snapshot": {
                "capabilities": {},
                "trajectory_source": "atif",
                "trajectory_wait_seconds": 0,
            },
        }
    )
    return (
        "INSERT INTO experiments (id, workspace_id, dataset_id, number, agent_id, created_by, "
        f"status, repetitions, config) VALUES ({exp_id}, 1, 1, {exp_id}, {agent_id}, 1, "
        f"'completed', 1, '{config}')"
    )


def _seed_at_0016(url: str, default_judge_url: str = URL) -> None:
    default_judge = json.dumps({"base_url": default_judge_url, "model": "m"})
    _exec(
        url,
        [
            "INSERT INTO users (id, tenant_id, username, password_hash, is_admin) "
            "VALUES (1, 'default', 'admin', 'x', true)",
            "INSERT INTO workspaces (id, tenant_id, name, owner_user_id) "
            "VALUES (1, 'default', 'admin', 1)",
            "INSERT INTO workspaces (id, tenant_id, name) VALUES (2, 'default', 'dev')",
            "INSERT INTO llm_providers (id, tenant_id, name, base_url) "
            f"VALUES (1, 'default', 'openai', '{URL}')",
            f"INSERT INTO tenant_settings (tenant_id, default_judge) "
            f"VALUES ('default', '{default_judge}')",
            "INSERT INTO datasets (id, workspace_id, name, kind) VALUES (1, 1, 'd', 'suite')",
            "INSERT INTO agents (id, workspace_id, name) VALUES (1, 1, 'mine')",
            "INSERT INTO agents (id, workspace_id, name) VALUES (2, 2, 'shared')",
            "INSERT INTO cases (id, tenant_id, case_id) VALUES (1, 'default', 'q-1')",
            _experiment(1, 1, {"base_url": URL, "model": "m"}),
            _experiment(2, 2, {"base_url": URL, "model": "m"}),
            _experiment(3, 2, {"base_url": "https://gone.example/v1", "model": "m"}),
            "INSERT INTO agent_runs (id, agent_id, harness_run_id, status, started_at, metrics) "
            "VALUES (1, 1, 'h1', 'completed', now(), '{}'), (2, 2, 'h2', 'completed', now(), '{}')",
            "INSERT INTO case_versions (case_ref, content_hash, input, evaluators, tags) "
            "VALUES (1, 'h1', '{}', '{}', '{}')",
            "INSERT INTO experiment_cases (experiment_id, case_ref, content_hash, "
            "effective_evaluators) VALUES (1, 1, 'h1', '{}'), (2, 1, 'h1', '{}')",
            "INSERT INTO experiment_runs (id, experiment_id, case_ref, repetition, agent_run_id) "
            "VALUES (1, 1, 1, 1, 1), (2, 2, 1, 1, 2)",
            "INSERT INTO evaluations (experiment_run_id, producer, name, kind, source) "
            "VALUES (1, 'a', 'a', 'score', 'builtin'), (2, 'a', 'a', 'score', 'builtin')",
        ],
    )


def test_0017_backfills_scored_on_and_stores_provider_judges(fresh_db_url):
    run_migrations(fresh_db_url, "0016")
    _seed_at_0016(fresh_db_url)
    run_migrations(fresh_db_url, "0017")
    assert _rows(
        fresh_db_url, "SELECT experiment_run_id, scored_on FROM evaluations ORDER BY 1"
    ) == [(1, "local"), (2, "server")]
    [(default_judge,)] = _rows(fresh_db_url, "SELECT default_judge FROM tenant_settings")
    assert default_judge == {"provider": "openai", "model": "m"}
    judges = dict(_rows(fresh_db_url, "SELECT id, config->'judge' FROM experiments"))
    assert judges[1] == {"base_url": URL, "model": "m"}
    assert judges[2] == {"provider": "openai", "model": "m"}
    assert judges[3] == {"base_url": "https://gone.example/v1", "model": "m"}


def test_0017_fails_when_the_default_judge_matches_no_provider(fresh_db_url):
    run_migrations(fresh_db_url, "0016")
    _seed_at_0016(fresh_db_url, default_judge_url="https://gone.example/v1")
    with pytest.raises(RuntimeError, match="https://gone.example/v1"):
        run_migrations(fresh_db_url, "0017")


def test_0017_downgrades(fresh_db_url):
    run_migrations(fresh_db_url, "0016")
    _seed_at_0016(fresh_db_url)
    run_migrations(fresh_db_url, "0017")
    downgrade_migrations(fresh_db_url, "0016")
    [(default_judge,)] = _rows(fresh_db_url, "SELECT default_judge FROM tenant_settings")
    assert default_judge == {"base_url": URL, "model": "m"}
    judges = dict(_rows(fresh_db_url, "SELECT id, config->'judge' FROM experiments"))
    assert judges[2] == {"base_url": URL, "model": "m"}
    run_migrations(fresh_db_url, "0017")
