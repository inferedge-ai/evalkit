from sqlalchemy import create_engine, inspect

from evalkit.db.session import downgrade_migrations, head_revision, run_migrations


def tables(url: str) -> set[str]:
    engine = create_engine(url)
    found = set(inspect(engine).get_table_names())
    engine.dispose()
    return found - {"alembic_version"}


def test_baseline_downgrades_to_an_empty_schema_and_back(fresh_db_url):
    run_migrations(fresh_db_url)
    created = tables(fresh_db_url)
    assert head_revision() == "0012"
    downgrade_migrations(fresh_db_url, "base")
    assert tables(fresh_db_url) == set()
    run_migrations(fresh_db_url)
    assert tables(fresh_db_url) == created
