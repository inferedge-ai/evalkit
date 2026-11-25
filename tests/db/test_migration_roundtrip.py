from sqlalchemy import create_engine, inspect

from evalkit.db.session import (
    JOBS_TABLE_PREFIX,
    downgrade_migrations,
    head_revision,
    run_migrations,
)


def tables(url: str) -> set[str]:
    engine = create_engine(url)
    found = set(inspect(engine).get_table_names())
    engine.dispose()
    return found - {"alembic_version"}


def test_baseline_downgrades_to_only_the_job_queue_tables_and_back(fresh_db_url):
    run_migrations(fresh_db_url)
    created = tables(fresh_db_url)
    assert head_revision() == "0026"
    downgrade_migrations(fresh_db_url, "base")
    assert {t for t in tables(fresh_db_url) if not t.startswith(JOBS_TABLE_PREFIX)} == set()
    run_migrations(fresh_db_url)
    assert tables(fresh_db_url) == created
