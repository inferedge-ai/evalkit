from sqlalchemy import create_engine, inspect

from evalkit.db.session import downgrade_migrations, run_migrations
from tests.db.test_migration_0016 import _exec, _rows

SEED = [
    "SET session_replication_role = replica",
    "INSERT INTO agent_runs (id, agent_id, harness_run_id, status, started_at, metrics) "
    "VALUES (10, 1, 'first-attempt', 'failed', now(), '{}'), "
    "(11, 1, 'second-attempt', 'completed', now(), '{}')",
    "INSERT INTO experiment_runs (id, experiment_id, case_ref, repetition, agent_run_id) "
    "VALUES (5, 1, 1, 1, 11)",
    "INSERT INTO evaluations (id, experiment_run_id, producer, name, kind, source, scored_on, "
    "value) VALUES (1, 5, 'answer_rubrics', 'answer_rubrics', 'score', 'builtin', 'server', "
    "0.5), (2, 5, 'gauge_turns', 'gauge_turns', 'gauge', 'builtin', 'server', 3)",
]


def test_0024_moves_evaluations_to_the_agent_run_of_their_experiment_run(fresh_db_url):
    run_migrations(fresh_db_url, "0023")
    _exec(fresh_db_url, SEED)
    run_migrations(fresh_db_url, "0024")
    assert _rows(fresh_db_url, "SELECT id, agent_run_id FROM evaluations ORDER BY id") == [
        (1, 11),
        (2, 11),
    ]
    assert _rows(fresh_db_url, "SELECT DISTINCT kind FROM agent_runs") == [("eval",)]


def test_0024_downgrade_restores_the_experiment_run_and_drops_app_run_evaluations(fresh_db_url):
    run_migrations(fresh_db_url, "0023")
    _exec(fresh_db_url, SEED)
    run_migrations(fresh_db_url, "0024")
    _exec(
        fresh_db_url,
        [
            "SET session_replication_role = replica",
            "INSERT INTO evaluations (id, agent_run_id, producer, name, kind, source, scored_on) "
            "VALUES (3, 10, 'gauge_turns', 'gauge_turns', 'gauge', 'builtin', 'server')",
        ],
    )
    downgrade_migrations(fresh_db_url, "0023")
    assert _rows(fresh_db_url, "SELECT id, experiment_run_id FROM evaluations ORDER BY id") == [
        (1, 5),
        (2, 5),
    ]


def test_upgrade_creates_the_job_queue_tables(fresh_db_url):
    run_migrations(fresh_db_url)
    engine = create_engine(fresh_db_url)
    tables = set(inspect(engine).get_table_names())
    engine.dispose()
    assert "procrastinate_jobs" in tables
